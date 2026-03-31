import streamlit as st
import pandas as pd
import requests
import time
import re
import io
import json
import xml.etree.ElementTree as ET

# --- KONFIGURACJA STRONY ---
st.set_page_config(page_title="Bibliotekarz 2.0", page_icon="📖", layout="wide")

# --- INTEGRACJA GOOGLE ANALYTICS 4 ---
GOOGLE_ANALYTICS_ID = "G-EYLDFL816H"

def inject_ga(ga_id):
    if ga_id.startswith("G-XXXX"): return 
    js = f"""
    <script>
    var parentHead = window.parent.document.head;
    if (!parentHead.querySelector('script[src*="gtag/js?id={ga_id}"]')) {{
        var script = window.parent.document.createElement('script');
        script.async = true;
        script.src = 'https://www.googletagmanager.com/gtag/js?id={ga_id}';
        parentHead.appendChild(script);
        var script2 = window.parent.document.createElement('script');
        script2.innerHTML = `
            window.parent.dataLayer = window.parent.dataLayer || [];
            function gtag(){{window.parent.dataLayer.push(arguments);}}
            gtag('js', new Date());
            gtag('config', '{ga_id}');
        `;
        parentHead.appendChild(script2);
    }}
    </script>
    """
    st.components.v1.html(js, height=0, width=0)

inject_ga(GOOGLE_ANALYTICS_ID)

# --- SŁOWNIK JĘZYKÓW ---
LANG_MAP = {
    "pol": "polski", "eng": "angielski", "ger": "niemiecki",
    "fre": "francuski", "rus": "rosyjski", "ita": "włoski",
    "spa": "hiszpański", "lat": "łacina", "cze": "czeski", "ukr": "ukraiński"
}

# --- POBIERANIE POŚWIADCZEŃ Z SECRETS ---
try:
    ELIBRI_USER = st.secrets["elibri"]["username"]
    ELIBRI_PASS = st.secrets["elibri"]["password"]
except Exception:
    st.error("❌ Brak konfiguracji Secrets (elibri.username / elibri.password)")
    st.stop()

# --- FUNKCJE POMOCNICZE ---
def reverse_authors(authors_str):
    if not authors_str or authors_str in ["Nieznany", "Brak", "Błąd danych"]:
        return authors_str
    parts = authors_str.split(",") 
    reversed_parts = []
    for part in parts:
        name_atoms = part.strip().split()
        if len(name_atoms) >= 2:
            last_name = name_atoms[-1]
            first_names = " ".join(name_atoms[:-1])
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

# --- 1. PARSER ONIX (eLibri) ---
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

# --- 2. OBSŁUGA OPEN LIBRARY ---
def fetch_open_library(isbn):
    try:
        url = f"https://openlibrary.org/api/books?bibkeys=ISBN:{isbn}&format=json&jscmd=data"
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            data = r.json()
            key = f"ISBN:{isbn}"
            if key in data:
                b = data[key]
                auth_list = [a.get('name') for a in b.get('authors', [])]
                authors_str = ", ".join(auth_list) if auth_list else "Nieznany"
                return {
                    "Tytuł": b.get('title', "Brak tytułu"),
                    "Autorzy": authors_str,
                    "Autorzy (Nazwisko Imię)": reverse_authors(authors_str),
                    "Oprawa": "Brak danych (OL)", "Język": "Brak danych (OL)",
                    "Kategoria": "Baza OpenLibrary", "Data premiery": b.get('publish_date', "Brak daty"),
                    "Seria": "Brak", "Opis wydania": "Brak",
                    "Wydawca": ", ".join([p.get('name') for p in b.get('publishers', [])]) or "Brak",
                    "Imprint": "Brak", "Liczba stron": str(b.get('number_of_pages', "Brak")),
                    "ISBN-13": isbn, "Cena": "n/d", "Opis": "Dane pobrane z OpenLibrary",
                    "Link do okładki": b.get('cover', {}).get('large', "Brak okładki")
                }
        return None
    except: return None

# --- 3. OBSŁUGA BIBLIOTEKI NAUKI ---
def fetch_biblioteka_nauki(isbn):
    try:
        url = f"https://bibliotekanauki.pl/api/v1/publications?filter[isbn]={isbn}"
        r = requests.get(url, timeout=15)
        if r.status_code == 200:
            res = r.json()
            data_list = res.get('data', [])
            if not data_list: return None
            
            book = data_list[0]
            attrs = book.get('attributes', {})
            
            auths = attrs.get('authors', [])
            auth_str = ", ".join([f"{a.get('firstname', '')} {a.get('surname', '')}".strip() for a in auths]) or "Nieznany"
            
            abstracts = attrs.get('abstracts', [])
            desc = abstracts[0].get('value', "Brak opisu") if abstracts else "Brak opisu"
            
            return {
                "Tytuł": attrs.get('title', "Brak tytułu"),
                "Autorzy": auth_str,
                "Autorzy (Nazwisko Imię)": reverse_authors(auth_str),
                "Oprawa": "Akademicka / Cyfrowa", "Język": attrs.get('language', "Polski (BN)"),
                "Kategoria": "Nauka / Akademickie", "Data premiery": str(attrs.get('year', "Brak")),
                "Seria": "Biblioteka Nauki", "Opis wydania": "Open Access",
                "Wydawca": attrs.get('publisher', {}).get('name', "Brak"),
                "Imprint": "Brak", "Liczba stron": "Brak danych",
                "ISBN-13": isbn, "Cena": "0.00", "Opis": desc,
                "Link do okładki": f"https://bibliotekanauki.pl/publications/{book.get('id')}"
            }
        return None
    except: return None

# --- GŁÓWNA LOGIKA KASKADOWA ---
def get_book_data(isbn):
    # 1. eLibri
    url_el = f"https://www.elibri.com.pl/distributors/empik/by_isbn/{isbn}"
    try:
        r = requests.get(url_el, auth=(ELIBRI_USER, ELIBRI_PASS), timeout=10)
        if r.status_code == 200:
            data = parse_onix_data(r.content)
            if data and data.get("Tytuł") != "Brak tytułu":
                return data, "eLibri"
    except: pass

    # 2. OpenLibrary
    ol_data = fetch_open_library(isbn)
    if ol_data: return ol_data, "OpenLibrary"

    # 3. Biblioteka Nauki
    bn_data = fetch_biblioteka_nauki(isbn)
    if bn_data: return bn_data, "Biblioteka Nauki"

    return None, "Nie znaleziono"

# --- UI STREAMLIT ---
st.title("📖 Bibliotekarz 2.0")
st.subheader("Multibaza: eLibri ➔ OpenLibrary ➔ Biblioteka Nauki")

uploaded_file = st.file_uploader("Załaduj plik Excel z kolumną ISBN", type=["xlsx"])

if uploaded_file:
    df_in = pd.read_excel(uploaded_file)
    target_col = st.selectbox("Wybierz kolumnę z numerami ISBN:", df_in.columns)
    
    if st.button("Pobierz dane (Kaskadowo)"):
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
            
            entry = {"Identyfikator": isbn_raw, "Źródło": source}
            for h in headers:
                entry[h] = book_info.get(h, "Brak") if book_info else "Brak"
            
            final_data.append(entry)
            progress_bar.progress((i + 1) / len(df_in))
            time.sleep(0.1)

        st.session_state.results_df = pd.DataFrame(final_data)
        st.success(f"Przetworzono {len(df_in)} rekordów!")

if 'results_df' in st.session_state:
    st.dataframe(st.session_state.results_df)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine='xlsxwriter') as writer:
        st.session_state.results_df.to_excel(writer, index=False)
    st.download_button("📥 Pobierz kompletny raport Excel", buf.getvalue(), "rejestr_bibliotekarz_2.0.xlsx")
