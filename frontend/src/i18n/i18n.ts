import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';
import LanguageDetector from 'i18next-browser-languagedetector';

// Import traduzioni
import itCommon from '../locales/it/common.json';
import itLicense from '../locales/it/license.json';
import itSettings from '../locales/it/settings.json';
import itAuth from '../locales/it/auth.json';
import itPrivacypa from '../locales/it/privacypa.json';

import enCommon from '../locales/en/common.json';
import enLicense from '../locales/en/license.json';
import enSettings from '../locales/en/settings.json';
import enAuth from '../locales/en/auth.json';
import enPrivacypa from '../locales/en/privacypa.json';

// Risorse traduzioni
const resources = {
  it: {
    common: itCommon,
    license: itLicense,
    settings: itSettings,
    auth: itAuth,
    privacypa: itPrivacypa,
  },
  en: {
    common: enCommon,
    license: enLicense,
    settings: enSettings,
    auth: enAuth,
    privacypa: enPrivacypa,
  },
};

// Configurazione i18next
i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources,
    fallbackLng: 'it',
    defaultNS: 'common',
    
    // Namespace disponibili
    ns: ['common', 'license', 'settings', 'auth', 'privacypa'],
    
    interpolation: {
      escapeValue: false, // React già escapa i valori
    },
    
    // Configurazione detection
    detection: {
      // Ordine di rilevamento lingua:
      // 1. localStorage (preferenza utente salvata)
      // 2. navigator (lingua browser)
      order: ['localStorage', 'navigator'],
      caches: ['localStorage'],
      lookupLocalStorage: 'app_language',
    },
    
    // React options
    react: {
      useSuspense: true,
    },
  });

/**
 * Imposta lingua da JWT payload (preferenza Portal).
 * Chiamare dopo login/token refresh.
 */
export const setLanguageFromToken = (preferredLanguage?: string) => {
  if (preferredLanguage && ['it', 'en'].includes(preferredLanguage)) {
    i18n.changeLanguage(preferredLanguage);
    localStorage.setItem('app_language', preferredLanguage);
  }
};

/**
 * Ritorna la lingua corrente.
 */
export const getCurrentLanguage = (): string => {
  return i18n.language || 'it';
};

/**
 * Cambia lingua corrente.
 */
export const changeLanguage = (lang: string) => {
  i18n.changeLanguage(lang);
  localStorage.setItem('app_language', lang);
};

export default i18n;
