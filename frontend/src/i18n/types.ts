/**
 * Type definitions per traduzioni.
 * 
 * Usa questi tipi per avere autocomplete e type safety nelle traduzioni.
 */

// Namespace disponibili
export type Namespace = 'common' | 'license' | 'settings' | 'auth' | 'privacypa';

// Chiavi traduzioni comuni (esempi - estendere se necessario)
export type CommonKey = 
  | 'app.title'
  | 'app.version'
  | 'navigation.home'
  | 'navigation.settings'
  | 'navigation.logout'
  | 'actions.save'
  | 'actions.cancel'
  | 'actions.delete'
  | 'actions.edit'
  | 'actions.add'
  | 'actions.refresh'
  | 'actions.sync'
  | 'status.loading'
  | 'status.error'
  | 'status.success'
  | 'messages.operation_success'
  | 'messages.operation_failed';

export type LicenseKey =
  | 'title'
  | 'status.valid'
  | 'status.expired'
  | 'status.missing'
  | 'sync_status.ok'
  | 'sync_status.using_cache'
  | 'sync_status.local_file'
  | 'fields.license_id'
  | 'fields.expires_at'
  | 'actions.sync_now';

export type SettingsKey =
  | 'title'
  | 'subtitle'
  | 'tabs.general'
  | 'tabs.users'
  | 'tabs.license'
  | 'tabs.advanced'
  | 'users.title'
  | 'users.add_user';

export type AuthKey =
  | 'login.title'
  | 'logout.confirm'
  | 'session.expired'
  | 'errors.unauthorized'
  | 'permissions.insufficient';

// Parametri interpolazione
export interface TranslationParams {
  [key: string]: string | number;
}

// Lingue supportate
export const SUPPORTED_LANGUAGES = [
  { code: 'it', name: 'Italiano', flag: '🇮🇹' },
  { code: 'en', name: 'English', flag: '🇬🇧' },
] as const;

export type LanguageCode = typeof SUPPORTED_LANGUAGES[number]['code'];
