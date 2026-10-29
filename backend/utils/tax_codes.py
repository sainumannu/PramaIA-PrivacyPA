"""Codice fiscale e partita IVA: forma e carattere di controllo.

Il codice fiscale di una persona fisica ha 16 caratteri (anche nella forma con omocodia), quello di un ente o di
una società 11 cifre, con lo stesso algoritmo della partita IVA. Una ditta individuale usa il codice del titolare.
"""
import re

_PERSON = re.compile(r"^[A-Z]{6}[0-9LMNPQRSTUV]{2}[ABCDEHLMPRST][0-9LMNPQRSTUV]{2}[A-Z][0-9LMNPQRSTUV]{3}[A-Z]$")
_NUMERIC = re.compile(r"^[0-9]{11}$")
_ODD = {
    **dict(zip("0123456789", (1, 0, 5, 7, 9, 13, 15, 17, 19, 21))),
    **dict(zip("ABCDEFGHIJKLMNOPQRSTUVWXYZ",
               (1, 0, 5, 7, 9, 13, 15, 17, 19, 21, 2, 4, 18, 20, 11, 3, 6, 8, 12, 14, 16, 10, 22, 25, 24, 23))),
}


def normalize(value: str | None) -> str | None:
    """Maiuscolo e senza spazi, punti o trattini; None se vuoto."""
    cleaned = re.sub(r"[\s.\-]", "", value or "").upper()
    return cleaned or None


def _even(ch: str) -> int:
    return int(ch) if ch.isdigit() else ord(ch) - ord("A")


def is_person_code(value: str) -> bool:
    """Codice fiscale di 16 caratteri con il carattere di controllo corretto."""
    if not _PERSON.match(value):
        return False
    total = sum(_ODD[ch] if i % 2 == 0 else _even(ch) for i, ch in enumerate(value[:15]))
    return chr(ord("A") + total % 26) == value[15]


def is_numeric_code(value: str) -> bool:
    """Codice fiscale numerico o partita IVA di 11 cifre con la cifra di controllo corretta."""
    if not _NUMERIC.match(value) or value == "0" * 11:
        return False
    total = 0
    for i, ch in enumerate(value[:10]):
        d = int(ch)
        if i % 2:
            d *= 2
            d -= 9 if d > 9 else 0
        total += d
    return (10 - total % 10) % 10 == int(value[10])


def check_identifiers(country_code: str | None, natural_person: bool, tax_id: str | None, vat_id: str | None) -> tuple[str | None, str | None]:
    """Verifica che il soggetto sia identificabile senza ambiguità e restituisce gli identificativi normalizzati.

    In Italia serve il codice fiscale: di 16 caratteri per una persona fisica, di 11 cifre (o del titolare, per una
    ditta individuale) per un ente o una società. All'estero basta un identificativo fiscale qualsiasi.
    """
    tax_id, vat_id = normalize(tax_id), normalize(vat_id)
    italian = (country_code or "IT").upper() == "IT"
    if not italian:
        if not tax_id and not vat_id:
            raise ValueError("Indicare l'identificativo fiscale del soggetto estero (codice fiscale o partita IVA del suo paese)")
        return tax_id, vat_id
    if vat_id and vat_id.startswith("IT"):
        vat_id = vat_id[2:]
    if not tax_id:
        raise ValueError("Indicare il codice fiscale: serve a identificare il soggetto senza ambiguità")
    if natural_person:
        if not is_person_code(tax_id):
            raise ValueError("Codice fiscale non valido: per una persona fisica servono 16 caratteri con il carattere di controllo corretto")
    elif not (is_numeric_code(tax_id) or is_person_code(tax_id)):
        raise ValueError("Codice fiscale non valido: per un ente o una società servono 11 cifre (16 caratteri per una ditta individuale)")
    if vat_id and not is_numeric_code(vat_id):
        raise ValueError("Partita IVA non valida: servono 11 cifre con la cifra di controllo corretta")
    return tax_id, vat_id
