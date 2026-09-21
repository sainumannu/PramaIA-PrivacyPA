import React from 'react';
import { Select, MenuItem, Box, Typography } from '@mui/material';
import { useTranslation } from '../i18n/useTranslation';
import { SUPPORTED_LANGUAGES, type LanguageCode } from '../i18n/types';
import { changeLanguage } from '../i18n/i18n';

interface LanguageSwitcherProps {
  variant?: 'standard' | 'outlined' | 'filled';
  size?: 'small' | 'medium';
  showLabel?: boolean;
}

/**
 * Componente per cambio lingua.
 * 
 * Mostra dropdown con lingue supportate e salva preferenza in localStorage.
 * 
 * @example
 * <LanguageSwitcher />
 * 
 * @example Con label
 * <LanguageSwitcher showLabel variant="outlined" size="small" />
 */
export const LanguageSwitcher: React.FC<LanguageSwitcherProps> = ({
  variant = 'standard',
  size = 'small',
  showLabel = false,
}) => {
  const { i18n } = useTranslation();
  const currentLanguage = i18n.language || 'it';

  const handleChange = (event: any) => {
    const newLang = event.target.value as LanguageCode;
    changeLanguage(newLang);
  };

  return (
    <Box display="flex" alignItems="center" gap={1}>
      {showLabel && (
        <Typography variant="body2" color="text.secondary">
          Lingua:
        </Typography>
      )}
      <Select
        value={currentLanguage}
        onChange={handleChange}
        variant={variant}
        size={size}
        sx={{ minWidth: 140 }}
      >
        {SUPPORTED_LANGUAGES.map((lang) => (
          <MenuItem key={lang.code} value={lang.code}>
            <Box display="flex" alignItems="center" gap={1}>
              <span>{lang.flag}</span>
              <span>{lang.name}</span>
            </Box>
          </MenuItem>
        ))}
      </Select>
    </Box>
  );
};

export default LanguageSwitcher;
