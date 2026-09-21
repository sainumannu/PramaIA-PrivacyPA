import { useTranslation as useI18nTranslation } from 'react-i18next';
import type { Namespace } from './types';

/**
 * Hook custom per traduzioni con TypeScript support.
 * 
 * Wrapper di react-i18next useTranslation con type hints.
 * 
 * @example
 * const { t } = useTranslation('license');
 * t('status.valid') // "Licenza valida" o "License valid"
 * 
 * @example Con parametri
 * t('messages.operation_failed', { error: 'Network error' })
 */
export const useTranslation = (namespace?: Namespace | Namespace[]) => {
  return useI18nTranslation(namespace);
};

export default useTranslation;
