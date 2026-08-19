"""
lists/nca_uk.py — Lista estática de UK NCA Most Wanted.

23 personas en la página https://www.nationalcrimeagency.gov.uk/most-wanted
(refrescable manualmente).

Esta lista se carga en SQLite y se busca localmente.
"""
from __future__ import annotations
from typing import Iterator

# Lista de 23 personas obtenidas de la página oficial.
# Cada entrada tiene slug (URL), nombre, y opcional descripción.
NCA_UK_MOST_WANTED = [
    {"slug": "alexander-kuksov", "name": "Alexander Kuksov"},
    {"slug": "allan-foster", "name": "Allan Foster"},
    {"slug": "charlie-salisbury", "name": "Charlie Salisbury"},
    {"slug": "christakis-philippou", "name": "Christakis Philippou"},
    {"slug": "daniel-dugic", "name": "Daniel Dugic"},
    {"slug": "dean-eighteen", "name": "Dean Eighteen"},
    {"slug": "derek-mcgraw-ferguson", "name": "Derek McGraw Ferguson"},
    {"slug": "francis-david-parker", "name": "Francis David Parker"},
    {"slug": "jack-mayle", "name": "Jack Mayle"},
    {"slug": "john-james-jones", "name": "John James Jones"},
    {"slug": "john-rocks", "name": "John Rocks"},
    {"slug": "jonathon-kelly", "name": "Jonathon Kelly"},
    {"slug": "kevin-thomas-parle", "name": "Kevin Thomas Parle"},
    {"slug": "liam-michael-murray", "name": "Liam Michael Murray"},
    {"slug": "matthew-purves", "name": "Matthew Purves"},
    {"slug": "osman-aydeniz", "name": "Osman Aydeniz"},
    {"slug": "ozgur-demir", "name": "Ozgur Demir"},
    {"slug": "philip-barry-foster", "name": "Philip Barry Foster"},
    {"slug": "rezgar-zengana", "name": "Rezgar Zengana"},
    {"slug": "shashi-dhar-sahnan", "name": "Shashi Dhar Sahnan"},
    {"slug": "shazad-ghafoor", "name": "Shazad Ghafoor"},
    {"slug": "spencer-dillon-lamb", "name": "Spencer Dillon Lamb"},
    {"slug": "timur-mehmet", "name": "Timur Mehmet"},
]


def search_nca_uk(nombre: str) -> list[dict]:
    """Busca en la lista de UK NCA por nombre (matching por tokens)."""
    from sources.local_lists import tokenize, normalize
    tokens = tokenize(nombre)
    if not tokens:
        return []
    results = []
    for entry in NCA_UK_MOST_WANTED:
        name_norm = normalize(entry["name"])
        if all(t in name_norm for t in tokens):
            results.append({
                "name": entry["name"],
                "slug": entry["slug"],
                "url": f"https://www.nationalcrimeagency.gov.uk/most-wanted/{entry['slug']}",
            })
    return results


def all_nca_uk() -> Iterator[dict]:
    """Itera sobre todos los entries para cargar en DB."""
    for entry in NCA_UK_MOST_WANTED:
        yield {
            "name": entry["name"],
            "slug": entry["slug"],
            "url": f"https://www.nationalcrimeagency.gov.uk/most-wanted/{entry['slug']}",
            "list": "UK NCA Most Wanted",
        }
