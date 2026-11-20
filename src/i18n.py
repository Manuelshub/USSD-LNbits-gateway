"""Menu translations.

English source strings are the lookup keys, so English output never changes
and a missing translation falls back to English. French text avoids characters
outside the GSM 7-bit alphabet (e.g. ç, ê, ô) that many feature phones cannot
render; é, è and à are safe.
"""

DEFAULT_LANGUAGE = "en"

# Language choices in the order they are offered on screen (1, 2, ...).
LANGUAGES = [("en", "English"), ("fr", "Francais")]

_FR = {
    # Main menu
    "Lightning Wallet": "Portefeuille Lightning",
    "Check Balance": "Voir le solde",
    "Receive Payment": "Recevoir",
    "Send Payment": "Envoyer",
    "History": "Historique",
    "Settings": "Paramètres",
    "Exit": "Quitter",
    "0:Menu": "0:Menu",
    "Thank you for using Lightning Wallet": "Merci d'avoir utilisé Portefeuille Lightning",
    # Balance / history
    "Balance: {amount} sats": "Solde: {amount} sats",
    "Fetching balance...": "Chargement du solde...",
    "Loading history...": "Chargement...",
    "No transactions yet": "Aucune transaction",
    "received": "reçu",
    "sent": "envoyé",
    # Receive
    "Enter amount in sats:\n(Example: 1000)": "Montant en sats:\n(Exemple: 1000)",
    "Your Lightning address:": "Votre adresse Lightning:",
    "Share it to get paid.": "Partagez-la pour être payé.",
    "1. Request an amount": "1. Demander un montant",
    "Generating invoice...": "Création de la facture...",
    "Invoice: {amount} sats": "Facture: {amount} sats",
    "Invoice for {amount} sats sent to you by SMS.": "Facture de {amount} sats envoyée par SMS.",
    "1:Status 0:Menu": "1:Statut 0:Menu",
    "Paid: {amount} sats": "Payé: {amount} sats",
    "Paid": "Payé",
    "Pending\n1:Check again": "En attente\n1:Vérifier",
    "Checking status...": "Vérification...",
    # Send
    "Send to:": "Envoyer à:",
    "1. Phone number": "1. Numéro de téléphone",
    "2. Lightning invoice": "2. Facture Lightning",
    "Enter recipient phone number:": "Numéro du destinataire:",
    "Invalid phone number.": "Numéro invalide.",
    "This number has no wallet yet.": "Ce numéro n'a pas de portefeuille.",
    "You cannot send to yourself.": "Vous ne pouvez pas vous envoyer.",
    "Amount to send in sats:": "Montant à envoyer en sats:",
    "Enter invoice to pay:": "Entrez la facture à payer:",
    "Confirm payment?": "Confirmer le paiement?",
    "Pay {amount} sats?": "Payer {amount} sats?",
    "Send {amount} sats to {recipient}?": "Envoyer {amount} sats à {recipient}?",
    "1:Confirm": "1:Confirmer",
    "Enter PIN to confirm:": "Entrez le PIN pour confirmer:",
    "Processing payment...": "Paiement en cours...",
    "Sent {amount} sats": "Envoyé: {amount} sats",
    "Sent {amount} sats to {recipient}": "Envoyé: {amount} sats à {recipient}",
    "Payment sent": "Paiement envoyé",
    "Payment failed": "Paiement échoué",
    # PIN / registration
    "Welcome to Lightning Wallet": "Bienvenue sur Portefeuille Lightning",
    "Choose language:": "Choisissez la langue:",
    "Create a 4-digit PIN:": "Créez un PIN à 4 chiffres:",
    "Re-enter your PIN:": "Confirmez votre PIN:",
    "PIN must be 4 digits.": "Le PIN doit avoir 4 chiffres.",
    "PIN too easy to guess.": "PIN trop facile.",
    "PINs do not match.": "Les PIN ne correspondent pas.",
    "Wrong PIN. Tries left: {left}": "PIN incorrect. Essais restants: {left}",
    "Too many wrong PINs. Try again in {minutes} min.": "Trop d'erreurs de PIN. Réessayez dans {minutes} min.",
    "Wallet created!": "Portefeuille créé!",
    "Any key: Menu": "Touche: Menu",
    "Registration is closed.": "Inscriptions fermées.",
    "Could not create wallet. Try later.": "Création impossible. Réessayez plus tard.",
    "Goodbye": "Au revoir",
    "Set a PIN in Settings first.": "Créez d'abord un PIN dans Paramètres.",
    # Settings
    "1. Language": "1. Langue",
    "2. Change PIN": "2. Changer le PIN",
    "Enter current PIN:": "PIN actuel:",
    "Enter new 4-digit PIN:": "Nouveau PIN à 4 chiffres:",
    "PIN changed.": "PIN modifié.",
    "Language updated.": "Langue modifiée.",
    # Errors and validation
    "1:Retry\n0:Menu": "1:Réessayer\n0:Menu",
    "An error occurred": "Une erreur est survenue",
    "Please enter numbers only": "Chiffres uniquement",
    "Amount must be positive": "Le montant doit être positif",
    "Amount too large (max: 100M sats)": "Montant trop élevé (max: 100M sats)",
    "Invalid invoice. Must start with 'ln'.": "Facture invalide. Doit commencer par 'ln'.",
    "Invalid invoice. Incomplete payment request.": "Facture invalide. Incomplète.",
    "Could not decode invoice": "Facture illisible",
    "Invalid invoice.": "Facture invalide.",
    "Insufficient balance": "Solde insuffisant",
    "LNbits service unavailable": "Service indisponible",
    "LNbits request timed out": "Délai dépassé",
    "Service unavailable": "Service indisponible",
    "LNbits service unavailable": "Service indisponible",
    "Invalid response from LNbits": "Service indisponible",
    "Unavailable": "Indisponible",
    "More:any key": "Suite:touche",
    # SMS
    "Lightning invoice for {amount} sats:\n{bolt11}": "Facture Lightning de {amount} sats:\n{bolt11}",
    "You received {amount} sats from {sender}.": "Vous avez reçu {amount} sats de {sender}.",
    "You received {amount} sats.": "Vous avez reçu {amount} sats.",
    "Dial {code} to use it.": "Composez {code} pour l'utiliser.",
}

_CATALOGS = {"fr": _FR}


def tr(lang: str, text: str, **kwargs) -> str:
    """Translate ``text`` into ``lang`` and fill in ``{placeholders}``."""
    translated = _CATALOGS.get(lang, {}).get(text, text)
    return translated.format(**kwargs) if kwargs else translated


def has_translation(lang: str, text: str) -> bool:
    """True when ``text`` can be shown in ``lang`` (English always can)."""
    return lang == DEFAULT_LANGUAGE or text in _CATALOGS.get(lang, {})


def is_supported(lang: str) -> bool:
    return any(code == lang for code, _ in LANGUAGES)
