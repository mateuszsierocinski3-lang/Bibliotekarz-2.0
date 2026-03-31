import streamlit as st
import pandas as pd
import requests
import time
import re
import io
import json
import xml.etree.ElementTree as ET

# --- KONFIGURACJA STRONY ---
st.set_page_config(page_title="Bibliotekarz Pro 2.0", page_icon="📖", layout="wide")

# --- SŁOWNIK JĘZYKÓW ---
LANG_MAP = {
    "pol": "polski", "eng": "angielski", "ger": "niemiecki",
    "fre": "francuski", "rus": "rosyjski", "ita": "włoski",
    "spa": "hiszpański", "lat": "łacina", "cze": "czeski", "ukr": "ukraiński"
}

# --- POBIERANIE POŚWIADCZEŃ ---
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

# --- 1. PARSER ELIBRI (ONIX) ---
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
        
        price_node = product.find('.//Price[PriceType="02"]')
        price_str = f"{find_text(price_node, 'PriceAmount')} {find_text(price_node, 'CurrencyCode')}" if price_node is not None else "Brak"

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
        if r.status_code == 200 and r.json():
            data = r.json().get(f"ISBN:{isbn}")
            if not data: return None
            
            authors = ", ".join([a.get('name') for a in data.get('authors', [])]) or "Nieznany"
            return {
                "Tytuł": data.get('title', "Brak tytułu"),
                "Autorzy": authors,
                "Autorzy (Nazwisko Imię)": reverse_authors(authors),
                "Oprawa": "Brak danych (OL)",
                "Język": "Brak danych (OL)",
                "Kategoria": "Baza OpenLibrary",
                "Data premiery": data.get('publish_date', "Brak"),
                "Seria": "Brak", "Opis wydania": "Brak",
                "Wydawca": ", ".join([p.get('name') for p in data.get('publishers', [])]) or "Brak",
                "Imprint": "Brak", "Liczba stron": str(data.get('number_of_pages', "Brak")),
                "ISBN-13": isbn, "Cena": "n/d", "Opis": "Dane z OpenLibrary",
                "Link do okładki": data.get('cover', {}).get('large', "Brak okładki")
            }
        return None
    except: return None

# --- 3. POPRAWIONA OBSŁUGA DOAB (DSpace REST API) ---
def fetch_doab(isbn):
    try:
        # Używamy oficjalnej składni REST z dokumentacji: expand=metadata
        search_url = f"https://directory.doabooks.org/rest/search?query=dc.identifier.isbn:%22{isbn}%22&expand=metadata"
        r = requests.get(search_url, headers={'Accept': 'application/json'}, timeout=15)
        
        if r.status_code == 200:
            results = r.json()
            if not results: return None
            
            book = results[0]
            metadata_list = book.get('metadata', [])
            
            # Helper do wyciągania wartości z listy metadata DOAB
            def get_meta(key):
                vals = [m['value'] for m in metadata_list if m['key'] == key]
                return vals[0] if vals else "Brak"

            title = get_meta('dc.title')
            authors = ", ".join([m['value'] for m in metadata_list if m['key'] == 'dc.contributor.author']) or "Nieznany"
            publisher = get_meta('dc.publisher')
            date = get_meta('dc.date.issued')
            desc = get_meta('dc.description.abstract')

            return {
                "Tytuł": title,
                "Autorzy": authors,
                "Autorzy (Nazwisko Imię)": reverse_authors(authors),
                "Oprawa": "Digital (DOAB)",
                "Język": "Brak (DOAB)",
                "Kategoria": "Open Access",
                "Data premiery": date,
                "Seria": "Brak", "Opis wydania": "Open Access",
                "Wydawca": publisher, "Imprint": "Brak", "Liczba stron": "Brak",
                "ISBN-13": isbn, "Cena": "0.00", "Opis": desc,
                "Link do okładki": f"https://directory.doabooks.org/handle/{book.get('handle')}"
            }
        return None
    except Exception as e:
        return None

# --- LOGIKA KASKADOWA ---
def get_book_data_cascading(isbn):
    # 1. ELIBRI
    try:
        r = requests.get(f"https://www.elibri.com.pl/distributors/empik/by_isbn/{isbn}", 
                         auth=(ELIBRI_USER, ELIBRI_PASS), timeout=10)
        if r.status_code == 200:
            data = parse_onix_data(r.content)
            if data and data.get("Tytuł") != "Brak tytułu":
                return data, "eLibri"
    except: pass

    # 2. OPENLIBRARY
    ol_data = fetch_open_library(isbn)
    if ol_data: return ol_data, "OpenLibrary"

    # 3. DOAB
    doab_data = fetch_doab(isbn)
    if doab_data: return doab_data, "DOAB"

    return None, "Nie znaleziono"

# --- UI STREAMLIT ---
st.title("📖 Bibliotekarz Pro 2.0 (eLibri ➔ OL ➔ DOAB)")

uploaded_file = st.file_uploader("Załaduj plik Excel z ISBN", type=["xlsx"])

if uploaded_file:
    df_in = pd.read_excel(uploaded_file)
    target_col = st.selectbox("Kolumna z ISBN:", df_in.columns)
    
    if st.button("Uruchom Pobieranie"):
        final_data = []
        progress = st.progress(0)
        
        headers = ["Tytuł", "Autorzy", "Autorzy (Nazwisko Imię)", "Oprawa", "Język", "Kategoria", 
                   "Data premiery", "Seria", "Opis wydania", "Wydawca", "Imprint", 
                   "Liczba stron", "ISBN-13", "Cena", "Opis", "Link do okładki"]
        
        for i, row in df_in.iterrows():
            isbn = str(row[target_col]).split('.')[0].strip()
            book_info, source = get_book_data_cascading(isbn)
            
            entry = {"Identyfikator": isbn, "Źródło": source}
            for h in headers:
                entry[h] = book_info.get(h, "Brak") if book_info else "Brak"
            
            final_data.append(entry)
            progress.progress((i + 1) / len(df_in))
            time.sleep(0.1)

        st.session_state.results_df = pd.DataFrame(final_data)
        st.success("Zakończono pobieranie!")

if 'results_df' in st.session_state:
    st.dataframe(st.session_state.results_df)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine='xlsxwriter') as writer:
        st.session_state.results_df.to_excel(writer, index=False)
    st.download_button("📥 Pobierz Excel", buf.getvalue(), "raport_ksiazek.xlsx")
