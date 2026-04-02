import streamlit as st
import pandas as pd
import requests
import time
import re
import io
import json
import xml.etree.ElementTree as ET

# --- KONFIGURACJA STRONY ---
st.set_page_config(page_title="Bibliotekarz Pro", page_icon="📖", layout="wide")

# --- INTEGRACJA GA4 ---
GOOGLE_ANALYTICS_ID = "G-EYZS8WJRXY"

def inject_ga(ga_id):
    """Wstrzykuje kod śledzący GA4 do aplikacji."""
    if ga_id.startswith("G-XXXX"): return 
    
    # Używamy standardowego wstrzykiwania do head poprzez iframe components
    js = f"""
    <script async src="https://www.googletagmanager.com/gtag/js?id={ga_id}"></script>
    <script>
        window.parent.dataLayer = window.parent.dataLayer || [];
        function gtag(){{window.parent.dataLayer.push(arguments);}}
        gtag('js', new Date());
        gtag('config', '{ga_id}', {{
            'page_path': window.parent.location.pathname,
            'cookie_flags': 'SameSite=None;Secure'
        }});
        
        // Funkcja dostępna globalnie dla track_event
        window.parent.gtag = gtag;
    </script>
    """
    st.components.v1.html(js, height=0)

def track_event(event_name, params=None):
    """Wysyła zdarzenie do GA4."""
    if params is None: params = {}
    params_json = json.dumps(params)
    js = f"""
    <script>
        if (window.parent.gtag) {{
            window.parent.gtag('event', '{event_name}', {params_json});
        }}
    </script>
    """
    st.components.v1.html(js, height=0)

# Inicjalizacja GA4
inject_ga(GOOGLE_ANALYTICS_ID)

# --- SŁOWNIK JĘZYKÓW ---
LANG_MAP = {
    "pol": "polski", "eng": "angielski", "ger": "niemiecki",
    "fre": "francuski", "rus": "rosyjski", "ita": "włoski",
    "spa": "hiszpański", "lat": "łacina", "cze": "czeski", "ukr": "ukraiński"
}

# --- POŚWIADCZENIA ---
try:
    ELIBRI_USER = st.secrets["elibri"]["username"]
    ELIBRI_PASS = st.secrets["elibri"]["password"]
except Exception:
    st.error("❌ Brak konfiguracji Secrets (elibri.username / elibri.password)")
    st.stop()

# --- FUNKCJE POMOCNICZE (bez zmian) ---
def reverse_authors(authors_str):
    if not authors_str or authors_str in ["Nieznany", "Brak", "Błąd danych"]:
        return authors_str
    parts = authors_str.split(",") 
    reversed_parts = []
    for part in parts:
        name_atoms = part.strip().split()
        if len(name_atoms) >= 2:
            last_name = name_atoms[-1]; first_names = " ".join(name_atoms[:-1])
            reversed_parts.append(f"{last_name} {first_names}")
        else:
            reversed_parts.append(part.strip())
    return ", ".join(reversed_parts)

def find_text(parent, path):
    node = parent.find(path)
    return node.text.strip() if node is not None and node.text else None

def format_date(date_str):
    if date_str and len(date_str) >= 8 and date_str.isdigit():
        return f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
    return date_str

# --- PARSER ONIX (ELIBRI) ---
def parse_onix_data(xml_content):
    try:
        xml_content_str = xml_content.decode('utf-8') if isinstance(xml_content, bytes) else xml_content
        xml_content_str = re.sub(r'\sxmlns="[^"]+"', '', xml_content_str, count=1)
        root = ET.fromstring(xml_content_str)
        product = root if root.tag == 'Product' else root.find('.//Product')
        if product is None: return None

        isbn13 = find_text(product, './/ProductIdentifier[ProductIDType="15"]/IDValue') or "Brak"
        title = find_text(product, './/TitleDetail[TitleType="01"]//TitleText') or "Brak tytułu"
        
        authors = []
        for contrib in product.findall('.//Contributor'):
            name = find_text(contrib, 'PersonName')
            if name: authors.append(name)
        authors_str = ", ".join(authors) if authors else "Nieznany"

        series_names = []
        for series in product.findall('.//Collection'):
            s_title = find_text(series, './/TitleText')
            if s_title: series_names.append(s_title)
        series_str = ", ".join(series_names) if series_names else "Brak serii"

        desc_detail = product.find('DescriptiveDetail')
        edition_display, pages, language_display, categories, oprawa = "Brak", "Brak", "Brak", [], "Nieznana"
        
        if desc_detail is not None:
            ed_stat = find_text(desc_detail, 'EditionStatement')
            if ed_stat: edition_display = "Pierwsze" if ed_stat == "1" else ed_stat
            pages = find_text(desc_detail, './/Extent[ExtentType="00"]/ExtentValue')
            p_form = find_text(desc_detail, 'ProductForm')
            p_detail = find_text(desc_detail, 'ProductFormDetail')
            if p_form == "BC": oprawa = "Miękka ze skrzydełkami" if p_detail == "B504" else "Miękka"
            elif p_form == "BB": oprawa = "Twarda"
            lang_node = desc_detail.find('.//Language[LanguageRole="01"]/LanguageCode')
            if lang_node is not None:
                l_code = lang_node.text.strip().lower()
                language_display = LANG_MAP.get(l_code, l_code.upper())
            for subject in desc_detail.findall('.//Subject'):
                cat_text = find_text(subject, 'SubjectHeadingText')
                if cat_text: categories.append(cat_text)
        categories_str = " | ".join(list(dict.fromkeys(categories))) if categories else "Brak kategorii"

        pub_date_raw = find_text(product, './/PublishingDate[PublishingDateRole="01"]/Date')
        release_date = format_date(pub_date_raw) or "Brak daty"

        description = "Brak opisu"
        text_content = product.find('.//TextContent[TextType="03"]/Text')
        if text_content is not None:
            description = re.sub('<[^<]+?>', '', text_content.text or "").strip()

        cover_url = "Brak okładki"
        res_link = product.find('.//SupportingResource[ResourceContentType="01"]//ResourceLink')
        if res_link is not None: cover_url = res_link.text

        publisher = find_text(product, './/Publisher/PublisherName') or "Brak"
        imprint = find_text(product, './/Imprint/ImprintName') or "Brak"
        
        price_str = "Brak"
        price_node = product.find('.//Price[PriceType="02"]')
        if price_node is not None:
            price_str = f"{find_text(price_node, 'PriceAmount')} {find_text(price_node, 'CurrencyCode')}"

        return {
            "Tytuł": title, "Autorzy": authors_str, "Autorzy (Nazwisko Imię)": reverse_authors(authors_str),
            "Oprawa": oprawa, "Język": language_display, "Kategoria": categories_str, "Data premiery": release_date,
            "Seria": series_str, "Opis wydania": edition_display, "Wydawca": publisher, "Imprint": imprint,
            "Liczba stron": pages, "ISBN-13": isbn13, "Cena": price_str, "Opis": description, "Link do okładki": cover_url
        }
    except Exception: return None

# --- OBSŁUGA OPEN LIBRARY ---
def fetch_open_library(isbn):
    try:
        url = f"https://openlibrary.org/api/books?bibkeys=ISBN:{isbn}&format=json&jscmd=data"
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            data = r.json()
            key = f"ISBN:{isbn}"
            if key in data:
                b = data[key]
                authors = [a.get('name') for a in b.get('authors', [])]
                authors_str = ", ".join(authors) if authors else "Nieznany"
                return {
                    "Tytuł": b.get('title', "Brak tytułu"),
                    "Autorzy": authors_str,
                    "Autorzy (Nazwisko Imię)": reverse_authors(authors_str),
                    "Oprawa": "Brak danych (OL)",
                    "Język": "Brak danych (OL)",
                    "Kategoria": " | ".join([s.get('name') for s in b.get('subjects', [])[:3]]) if b.get('subjects') else "Brak",
                    "Data premiery": b.get('publish_date', "Brak daty"),
                    "Seria": "Brak danych (OL)",
                    "Opis wydania": "Brak danych (OL)",
                    "Wydawca": ", ".join([p.get('name') for p in b.get('publishers', [])]) if b.get('publishers') else "Brak",
                    "Imprint": "Brak",
                    "Liczba stron": str(b.get('number_of_pages', "Brak")),
                    "ISBN-13": isbn,
                    "Cena": "Nie dotyczy (OL)",
                    "Opis": b.get('notes', "Brak opisu"),
                    "Link do okładki": b.get('cover', {}).get('large', "Brak okładki")
                }
        return None
    except: return None

# --- GŁÓWNA LOGIKA POBIERANIA ---
def get_book_data(isbn):
    url_elibri = f"https://www.elibri.com.pl/distributors/empik/by_isbn/{isbn}"
    try:
        r = requests.get(url_elibri, auth=(ELIBRI_USER, ELIBRI_PASS), timeout=10)
        if r.status_code == 200:
            data = parse_onix_data(r.content)
            if data: return data, "eLibri"
    except: pass

    ol_data = fetch_open_library(isbn)
    if ol_data:
        return ol_data, "OpenLibrary"
    
    return None, "Brak"

# --- UI STREAMLIT ---
st.title("📖 Multibaza Książkowa (eLibri + OpenLibrary)")

uploaded_file = st.file_uploader("Załaduj plik Excel z kolumną ISBN", type=["xlsx"])

if uploaded_file:
    df_in = pd.read_excel(uploaded_file)
    target_col = st.selectbox("Wybierz kolumnę z numerami ISBN:", df_in.columns)
    
    if st.button("Pobierz dane z API"):
        # ŚLEDZENIE: Rozpoczęcie pobierania
        track_event("api_request_start", {"row_count": len(df_in)})
        
        final_data = []
        progress_bar = st.progress(0)
        
        headers = [
            "Tytuł", "Autorzy", "Autorzy (Nazwisko Imię)", "Oprawa", "Język", "Kategoria", 
            "Data premiery", "Seria", "Opis wydania", "Wydawca", "Imprint", 
            "Liczba stron", "ISBN-13", "Cena", "Opis", "Link do okładki"
        ]
        
        for i, row in df_in.iterrows():
            isbn_raw = str(row[target_col]).split('.')[0].strip()
            book_info, source = get_book_data(isbn_raw)
            entry = {"Identyfikator": isbn_raw, "Źródło danych": source}
            
            for h in headers:
                if book_info:
                    entry[h] = book_info.get(h, "Brak")
                else:
                    entry[h] = "Nie znaleziono w żadnej bazie"
            
            final_data.append(entry)
            progress_bar.progress((i + 1) / len(df_in))
            time.sleep(0.1)

        st.session_state.results_df = pd.DataFrame(final_data)
        st.success("Przetwarzanie zakończone!")
        
        # ŚLEDZENIE: Zakończenie sukcesem
        track_event("api_request_success", {"processed_items": len(final_data)})

if 'results_df' in st.session_state:
    st.dataframe(st.session_state.results_df)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine='xlsxwriter') as writer:
        st.session_state.results_df.to_excel(writer, index=False)
    
    if st.download_button("📥 Pobierz kompletny Excel", buf.getvalue(), "rejestr_ksiazek.xlsx"):
        # ŚLEDZENIE: Pobranie pliku
        track_event("file_downloaded")
