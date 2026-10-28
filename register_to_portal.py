#!/usr/bin/env python3
"""
Script per registrare automaticamente l'applicativo su PramaIA-Portal.

Uso:
    python register_to_portal.py [--admin-token TOKEN]

Se --admin-token non è fornito, lo script chiederà di inserirlo interattivamente.
Il token admin può essere ottenuto dal Portal dopo il login come amministratore.
"""

import os
import sys
import argparse
import requests
from dotenv import load_dotenv

# Carica variabili d'ambiente
load_dotenv()

APP_ID = os.getenv("APP_ID", "PramaIA-PrivacyManager")
APP_NAME = os.getenv("APP_NAME", "PrivacyManager")
PORTAL_URL = os.getenv("PORTAL_URL", "http://localhost:3080")
BACKEND_PORT = os.getenv("BACKEND_PORT", "8003")
FRONTEND_PORT = os.getenv("FRONTEND_PORT", "3003")

# Parametri statici dal template
APP_DESCRIPTION = "Gestione Privacy e GDPR PramaIA"
APP_ICON = "🛡️"
APP_COLOR = "#10b981"


def register_app(admin_token: str) -> bool:
    """
    Registra l'applicativo sul Portal tramite API.
    
    Args:
        admin_token: Token JWT di un utente amministratore del Portal
        
    Returns:
        True se la registrazione ha successo, False altrimenti
    """
    
    # Costruisci URL dell'app
    app_url = f"http://localhost:{FRONTEND_PORT}"
    backend_url = f"http://localhost:{BACKEND_PORT}"
    
    # Payload di registrazione
    payload = {
        "app_id": APP_ID,
        "name": APP_NAME,
        "description": APP_DESCRIPTION,
        "icon": APP_ICON,
        "color": APP_COLOR,
        "url": app_url,
        "backend_url": backend_url,
        "enabled": True,
        # Collegamenti in ingresso (ModuleLink del Portal): il Portal emette all'HelpDesk i token con
        # audience = PrivacyPA solo se il collegamento è attivo (vedi INTEGRAZIONE_HELPDESK.md).
        "incoming_links_manifest": [
            {"source_app_id": "pramaia-helpdesk", "purpose": "access_requests", "path": "/api/privacypa"},
        ],
    }
    
    # Headers con autenticazione
    headers = {
        "Authorization": f"Bearer {admin_token}",
        "Content-Type": "application/json",
    }
    
    # Endpoint di registrazione
    register_url = f"{PORTAL_URL}/api/apps"
    
    print(f"📡 Registrazione di '{APP_NAME}' su {PORTAL_URL}...")
    print(f"   ID: {APP_ID}")
    print(f"   URL: {app_url}")
    print(f"   Backend: {backend_url}")
    print()
    
    try:
        response = requests.post(register_url, json=payload, headers=headers, timeout=10)
        
        if response.status_code == 201:
            print("✅ Applicativo registrato con successo!")
            print()
            print("Prossimi passi:")
            print(f"1. Accedi al Portal: {PORTAL_URL}")
            print(f"2. Vai alla sezione 'Applicazioni' per gestire permessi e utenti")
            print(f"3. Assegna l'accesso agli utenti che devono usare {APP_NAME}")
            return True
            
        elif response.status_code == 409:
            print("ℹ️  L'applicativo è già registrato nel Portal")
            # Aggiorna solo i collegamenti in entrata: il Portal crea quelli mancanti (es. HelpDesk → PrivacyPA
            # dopo un aggiornamento). Il resto della configurazione resta quello impostato dall'amministratore.
            update = requests.patch(
                f"{register_url}/{APP_ID}",
                json={"incoming_links_manifest": payload["incoming_links_manifest"]},
                headers=headers, timeout=10,
            )
            if update.status_code == 200:
                print("✅ Collegamenti tra moduli aggiornati (quelli delle app già registrate sono attivi)")
                print()
                print("Per aggiornare il resto della configurazione usa l'interfaccia admin del Portal.")
                return True
            print(f"❌ Aggiornamento dei collegamenti non riuscito: {update.status_code}")
            print(f"   Risposta: {update.text}")
            print("   Crea il collegamento a mano nel Portal, in 'Collegamenti tra moduli'.")
            return False
            
        elif response.status_code == 401:
            print("❌ Token di autenticazione non valido o scaduto")
            print()
            print("Assicurati di:")
            print(f"1. Essere loggato come amministratore sul Portal ({PORTAL_URL})")
            print("2. Aver copiato correttamente il token JWT dal localStorage del browser")
            return False
            
        elif response.status_code == 403:
            print("❌ Permessi insufficienti")
            print()
            print("Solo gli amministratori possono registrare nuovi applicativi.")
            print(f"Assicurati di essere loggato come admin sul Portal ({PORTAL_URL})")
            return False
            
        else:
            print(f"❌ Errore durante la registrazione: {response.status_code}")
            print(f"   Risposta: {response.text}")
            return False
            
    except requests.exceptions.ConnectionError:
        print(f"❌ Impossibile connettersi al Portal su {PORTAL_URL}")
        print()
        print("Verifica che:")
        print("1. Il Portal sia in esecuzione")
        print("2. L'URL del Portal in .env sia corretto (PORTAL_URL)")
        return False
        
    except Exception as e:
        print(f"❌ Errore imprevisto: {e}")
        return False


def get_admin_token(args_token: str = None) -> str:
    """
    Ottiene il token admin da argomenti CLI o input utente.
    
    Args:
        args_token: Token passato come argomento CLI (opzionale)
        
    Returns:
        Token JWT dell'amministratore
    """
    if args_token:
        return args_token
    
    print("=" * 70)
    print("REGISTRAZIONE APPLICATIVO SU PRAMAIA-PORTAL")
    print("=" * 70)
    print()
    print("Per registrare l'applicativo serve un token JWT di amministratore.")
    print()
    print("Come ottenerlo:")
    print(f"1. Accedi al Portal come admin: {PORTAL_URL}")
    print("2. Apri gli strumenti sviluppatore del browser (F12)")
    print("3. Vai su Console e digita: localStorage.getItem('token')")
    print("4. Copia il token (senza virgolette) e incollalo qui sotto")
    print()
    
    token = input("Token JWT admin: ").strip()
    
    if not token:
        print("\n❌ Token non fornito. Uscita.")
        sys.exit(1)
        
    return token


def main():
    parser = argparse.ArgumentParser(
        description="Registra l'applicativo su PramaIA-Portal"
    )
    parser.add_argument(
        "--admin-token",
        type=str,
        help="Token JWT di un amministratore del Portal",
    )
    
    args = parser.parse_args()
    
    # Ottieni token admin
    admin_token = get_admin_token(args.admin_token)
    
    # Esegui registrazione
    success = register_app(admin_token)
    
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
