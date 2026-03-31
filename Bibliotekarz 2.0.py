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

# --- FUNKCJE POMOCNICZE (BEZ ZMIAN) ---
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

        # ... (reszta logiki parsowania eLibri z Twojego pierwotnego kodu)
        return {
            "Tytuł": title, "Autorzy": authors_str, "Autorzy (Nazwisko Imię)": reverse_authors(authors_str),
            "Oprawa": "Zdefiniowana w eLibri", "Język": "Polski (EL)", "Kategoria": "Baza eLibri",
            "Data premiery": "Brak", "Seria": "Brak", "Opis wydania": "Brak", "Wydawca": "Brak", 
            "Imprint": "Brak", "Liczba stron": "Brak", "ISBN-13": isbn13, "Cena": "Brak", 
            "Opis": "Brak", "Link do okładki": "Brak"
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
                    "Oprawa": "Brak (OL)",
                    "Język": "Brak (OL)",
                    "Kategoria": "OpenLibrary",
                    "Data premiery": b.get('publish_date', "Brak"),
                    "Seria": "Brak",
                    "Opis wydania": "Brak",
                    "Wydawca": ", ".join([p.get('name') for p in b.get('publishers', [])]),
                    "Imprint": "Brak",
                    "Liczba stron": str(b.get('number_of_pages', "Brak")),
                    "ISBN-13": isbn,
                    "Cena": "n/d",
                    "Opis": "Pobrano z OpenLibrary",
                    "Link do okładki": b.get('cover', {}).get('large', "Brak")
                }
        return None
    except: return None

# --- 3. OBSŁUGA DOAB ---
def fetch_doab(isbn):
    try:
        # DOAB API pozwala na wyszukiwanie po ISBN w polu 'metadata'
        url = f"https://directory.doabooks.org/rest/search?query=dc.identifier.isbn:{isbn}"
        headers = {'Accept': 'application/json'}
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code == 200:
            results = r.json()
            if results:
                book = results[0] # Bierzemy pierwszy traf
                metadata = {m['key']: m['value'] for m in book.get('metadata', [])}
                
                title = metadata.get('dc.title', "Brak tytułu")
                authors_str = metadata.get('dc.contributor.author', "Nieznany")
                publisher = metadata.get('dc.publisher', "Brak")
                date = metadata.get('dc.date.issued', "Brak")
                desc = metadata.get('dc.description.abstract', "Brak opisu")

                return {
                    "Tytuł": title,
                    "Autorzy": authors_str,
                    "Autorzy (Nazwisko Imię)": reverse_authors(authors_str),
                    "Oprawa": "Open Access (DOAB)",
                    "Język": "Brak (DOAB)",
                    "Kategoria": "DOAB Books",
                    "Data premiery": date,
                    "Seria": "Brak",
                    "Opis wydania": "Digital / Open Access",
                    "Wydawca": publisher,
                    "Imprint": "Brak",
                    "Liczba stron": "Brak",
                    "ISBN-13": isbn,
                    "Cena": "0.00 (OA)",
                    "Opis": desc[:500] + "..." if desc else "Brak",
                    "Link do okładki": f"https://directory.doabooks.org/handle/{book.get('handle')}"
                }
        return None
    except: return None

# --- LOGIKA KASKADOWA ---
def get_book_data_cascading(isbn):
    # KROK 1: ELIBRI
    url = f"https://www.elibri.com.pl/distributors/empik/by_isbn/{isbn}"
    try:
        r = requests.get(url, auth=(ELIBRI_USER, ELIBRI_PASS), timeout=10)
        if r.status_code == 200:
            data = parse_onix_data(r.content)
            if data: return data, "eLibri"
    except: pass

    # KROK 2: OPENLIBRARY
    ol_data = fetch_open_library(isbn)
    if ol_data: return ol_data, "OpenLibrary"

    # KROK 3: DOAB
    doab_data = fetch_doab(isbn)
    if doab_data: return doab_data, "DOAB"

    return None, "Brak"

# --- UI STREAMLIT ---
st.title("📖 Wielobazowy Pobieracz Danych (eLibri -> OL -> DOAB)")

uploaded_file = st.file_uploader("Załaduj plik Excel z kolumną ISBN", type=["xlsx"])

if uploaded_file:
    df_in = pd.read_excel(uploaded_file)
    target_col = st.selectbox("Wybierz kolumnę z numerami ISBN:", df_in.columns)
    
    if st.button("Pobierz dane ze wszystkich baz"):
        final_data = []
        progress_bar = st.progress(0)
        
        headers = [
            "Tytuł", "Autorzy", "Autorzy (Nazwisko Imię)", "Oprawa", "Język", "Kategoria", 
            "Data premiery", "Seria", "Opis wydania", "Wydawca", "Imprint", 
            "Liczba stron", "ISBN-13", "Cena", "Opis", "Link do okładki"
        ]
        
        for i, row in df_in.iterrows():
            isbn_raw = str(row[target_col]).split('.')[0].strip()
            
            book_info, source = get_book_data_cascading(isbn_raw)
            
            entry = {"Identyfikator": isbn_raw, "Źródło": source}
            for h in headers:
                if book_info:
                    entry[h] = book_info.get(h, "Brak")
                else:
                    entry[h] = "Nie znaleziono"
            
            final_data.append(entry)
            progress_bar.progress((i + 1) / len(df_in))
            time.sleep(0.1)

        st.session_state.results_df = pd.DataFrame(final_data)
        st.success("Gotowe!")

if 'results_df' in st.session_state:
    st.dataframe(st.session_state.results_df)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine='xlsxwriter') as writer:
        st.session_state.results_df.to_excel(writer, index=False)
    st.download_button("📥 Pobierz Excel", buf.getvalue(), "rejestr_ksiazek_v2.xlsx")
