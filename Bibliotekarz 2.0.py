import streamlit as st
import pandas as pd
import requests
import time
import re
import io
import xml.etree.ElementTree as ET

# --- KONFIGURACJA STRONY ---
st.set_page_config(page_title="Bibliotekarz 2.0", page_icon="📖", layout="wide")

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

# --- 1. PARSER eLibri (ONIX) ---
def parse_onix_data(xml_content):
    try:
        xml_content_str = xml_content.decode('utf-8') if isinstance(xml_content, bytes) else xml_content
        xml_content_str = re.sub(r'\sxmlns="[^"]+"', '', xml_content_str, count=1)
        root = ET.fromstring(xml_content_str)
        product = root if root.tag == 'Product' else root.find('.//Product')
        if product is None: return None

        isbn13 = find_text(product, './/ProductIdentifier[ProductIDType="15"]/IDValue') or "Brak"
        title = find_text(product, './/TitleDetail[TitleType="01"]//TitleText') or "Brak tytułu"
        
        authors = [contrib.find('PersonName').text for contrib in product.findall('.//Contributor') if contrib.find('PersonName') is not None]
        authors_str = ", ".join(authors) if authors else "Nieznany"

        desc_detail = product.find('DescriptiveDetail')
        edition, pages, language, oprawa = "Brak", "Brak", "Brak", "Nieznana"
        
        if desc_detail is not None:
            pages = find_text(desc_detail, './/Extent[ExtentType="00"]/ExtentValue') or "Brak"
            p_form = find_text(desc_detail, 'ProductForm')
            oprawa = "Twarda" if p_form == "BB" else "Miękka"
            lang_node = desc_detail.find('.//Language[LanguageRole="01"]/LanguageCode')
            if lang_node is not None:
                language = LANG_MAP.get(lang_node.text.strip().lower(), lang_node.text.upper())

        description = "Brak opisu"
        text_content = product.find('.//TextContent[TextType="03"]/Text')
        if text_content is not None:
            description = re.sub('<[^<]+?>', '', text_content.text or "").strip()

        cover_url = "Brak okładki"
        res_link = product.find('.//SupportingResource[ResourceContentType="01"]//ResourceLink')
        if res_link is not None: cover_url = res_link.text

        publisher = find_text(product, './/Publisher/PublisherName') or "Brak"

        return {
            "Tytuł": title, "Autorzy": authors_str, "Autorzy (Nazwisko Imię)": reverse_authors(authors_str),
            "Oprawa": oprawa, "Język": language, "Kategoria": "Baza eLibri", 
            "Data premiery": format_date(find_text(product, './/PublishingDate/Date')),
            "Seria": "Standard", "Opis wydania": "eLibri", "Wydawca": publisher, 
            "Imprint": "Brak", "Liczba stron": pages, "ISBN-13": isbn13, "Cena": "n/d", 
            "Opis": description, "Link do okładki": cover_url
        }
    except: return None

# --- 2. OBSŁUGA OPEN LIBRARY ---
def fetch_open_library(isbn):
    try:
        r = requests.get(f"https://openlibrary.org/api/books?bibkeys=ISBN:{isbn}&format=json&jscmd=data", timeout=10)
        if r.status_code == 200 and r.json():
            data = r.json().get(f"ISBN:{isbn}")
            if not data: return None
            auths = ", ".join([a.get('name') for a in data.get('authors', [])]) or "Nieznany"
            return {
                "Tytuł": data.get('title', "Brak tytułu"), "Autorzy": auths, "Autorzy (Nazwisko Imię)": reverse_authors(auths),
                "Oprawa": "Brak danych (OL)", "Język": "Brak danych (OL)", "Kategoria": "OpenLibrary", 
                "Data premiery": data.get('publish_date', "Brak"),
                "Seria": "Baza OL", "Opis wydania": "Brak", 
                "Wydawca": ", ".join([p.get('name') for p in data.get('publishers', [])]), 
                "Imprint": "Brak", "Liczba stron": str(data.get('number_of_pages', "Brak")), 
                "ISBN-13": isbn, "Cena": "n/d", "Opis": "Dane z OpenLibrary", 
                "Link do okładki": data.get('cover', {}).get('large', "Brak okładki")
            }
    except: return None

# --- 3. OBSŁUGA DOAB (PRISM API - WERSJA UŻYTKOWNIKA) ---
def fetch_doab(isbn):
    try:
        # Usuwamy myślniki i spacji dla pewności
        clean_isbn = re.sub(r'[-\s]', '', str(isbn))
        url = f"https://directory.doabooks.org/rest/peerReviews?isbn={clean_isbn}"
        
        # Nagłówki udające przeglądarkę
        headers = {
            'Accept': 'application/json',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        r = requests.get(url, headers=headers, timeout=15)
        
        if r.status_code == 200:
            data = r.json()
            if not data or len(data) == 0: return None
            
            # Pobieramy info o recenzji i przedmiocie
            review_info = data[0]
            item = review_info.get('item', {})
            metadata_list = item.get('metadata', [])
            
            # Słownik metadanych
            meta = {m['key']: m['value'] for m in metadata_list}
            
            # Autorzy
            authors_list = [m['value'] for m in metadata_list if m['key'] == 'dc.contributor.author']
            authors_str = ", ".join(authors_list) if authors_list else "Nieznany"

            return {
                "Tytuł": meta.get('dc.title', "Tytuł z PRISM"),
                "Autorzy": authors_str,
                "Autorzy (Nazwisko Imię)": reverse_authors(authors_str),
                "Oprawa": "Digital OA", "Język": meta.get('dc.language.iso', "Brak danych"),
                "Kategoria": "Recenzowane (PRISM)", "Data premiery": meta.get('dc.date.issued', "Brak"),
                "Seria": f"Typ: {review_info.get('type', 'Brak')}", "Opis wydania": "DOAB PRISM",
                "Wydawca": meta.get('dc.publisher', "Brak"), "Imprint": "Brak", "Liczba stron": "n/d",
                "ISBN-13": isbn, "Cena": "0.00", 
                "Opis": meta.get('dc.description.abstract', "Brak opisu w PRISM"),
                "Link do okładki": f"https://directory.doabooks.org/handle/{item.get('handle', '')}"
            }
        return None
    except: return None

# --- LOGIKA KASKADOWA ---
def get_book_data_cascading(isbn):
    # 1. eLibri
    try:
        r = requests.get(f"https://www.elibri.com.pl/distributors/empik/by_isbn/{isbn}", 
                         auth=(ELIBRI_USER, ELIBRI_PASS), timeout=10)
        if r.status_code == 200:
            data = parse_onix_data(r.content)
            if data: return data, "eLibri"
    except: pass

    # 2. OpenLibrary
    ol = fetch_open_library(isbn)
    if ol: return ol, "OpenLibrary"

    # 3. DOAB (PRISM)
    doab = fetch_doab(isbn)
    if doab: return doab, "DOAB"

    return None, "Brak"

# --- INTERFEJS STREAMLIT ---
st.title("📖 Bibliotekarz 2.0")
st.markdown("### Multibaza: eLibri ➔ OpenLibrary ➔ DOAB (PRISM)")

uploaded_file = st.file_uploader("Załaduj plik Excel z kolumną ISBN", type=["xlsx"])

if uploaded_file:
    df = pd.read_excel(uploaded_file)
    col = st.selectbox("Wybierz kolumnę z ISBN:", df.columns)
    
    if st.button("Pobierz dane (Kaskadowo)"):
        results = []
        bar = st.progress(0)
        
        headers = ["Tytuł", "Autorzy", "Autorzy (Nazwisko Imię)", "Oprawa", "Język", "Kategoria", 
                   "Data premiery", "Seria", "Opis wydania", "Wydawca", "Imprint", 
                   "Liczba stron", "ISBN-13", "Cena", "Opis", "Link do okładki"]
        
        for i, row in df.iterrows():
            isbn = str(row[col]).split('.')[0].strip()
            data, source = get_book_data_cascading(isbn)
            
            entry = {"ISBN wejściowy": isbn, "Źródło": source}
            for h in headers:
                entry[h] = data.get(h, "Brak") if data else "Brak danych"
            results.append(entry)
            bar.progress((i + 1) / len(df))
            time.sleep(0.1)
            
        res_df = pd.DataFrame(results)
        st.dataframe(res_df)
        
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            res_df.to_excel(writer, index=False)
        st.download_button("📥 Pobierz kompletny raport Excel", output.getvalue(), "bibliotekarz_2.0_export.xlsx")
