import React, { useEffect, useState } from 'react';
import {
  Alert, Box, Button, Checkbox, FormControl, FormControlLabel, InputLabel, MenuItem, Paper, Select, Switch, TextField, Typography,
} from '@mui/material';
import { apiClient } from '../services/apiClient';
import { useTranslation } from '../i18n/useTranslation';
import { ErrorBanner } from './ErrorBanner';

interface LevelRule { roles: string[]; step_up: boolean; watermark: string; max_downloads_per_hour: number | null; block_bulk_export: boolean }
interface Config { levels: Record<string, LevelRule>; step_up: { max_age_seconds: number; admin_actions: boolean } }
interface Payload { row_version: number; config: Config; levels: string[]; watermark_modes: string[]; roles: string[] }

const ALWAYS = ['SUPER_ADMIN', 'TENANT_ADMIN'];

// Regole per livello di riservatezza, filigrana, tetto ai download, blocco dell'export massivo e step-up (§134-§136).
export const ConfidentialityPanel: React.FC = () => {
  const { t } = useTranslation('privacypa');
  const [data, setData] = useState<Payload | null>(null);
  const [config, setConfig] = useState<Config | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);

  const apply = (payload: Payload) => { setData(payload); setConfig(payload.config); };
  useEffect(() => {
    apiClient.get<Payload>('/api/privacypa/policy/confidentiality').then(apply).catch((err) => setError(err.message));
  }, []);
  if (!data || !config) return error ? <Alert severity="error">{error}</Alert> : null;

  const setRule = (level: string, patch: Partial<LevelRule>) =>
    setConfig({ ...config, levels: { ...config.levels, [level]: { ...config.levels[level], ...patch } } });
  const save = () =>
    apiClient.put<Payload>('/api/privacypa/policy/confidentiality', config, { version: data.row_version })
      .then((next) => { apply(next); setError(null); setInfo(t('confidentiality.saved')); })
      .catch((err) => { setInfo(null); setError(err.message); });

  return (
    <Paper sx={{ p: 2, mt: 3 }}>
      <Typography variant="h6">{t('confidentiality.title')}</Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>{t('confidentiality.subtitle')}</Typography>
      <ErrorBanner message={error} onClose={() => setError(null)} />
      {info && <Alert severity="success" sx={{ mb: 2 }} onClose={() => setInfo(null)}>{info}</Alert>}

      {data.levels.map((level) => {
        const rule = config.levels[level];
        return (
          <Box key={level} sx={{ mb: 3 }}>
            <Typography variant="subtitle1" fontWeight={600}>{t(`register.confidentiality.${level}`)}</Typography>
            <Typography variant="caption" color="text.secondary">{t('confidentiality.roles_hint')}</Typography>
            <Box sx={{ display: 'flex', flexWrap: 'wrap' }}>
              {data.roles.map((role) => (
                <FormControlLabel
                  key={role} label={t(`policy.roles.${role}`)} sx={{ minWidth: 190 }}
                  control={(
                    <Checkbox
                      size="small" checked={rule.roles.includes(role) || ALWAYS.includes(role)} disabled={ALWAYS.includes(role)}
                      onChange={(e) => setRule(level, { roles: e.target.checked ? [...rule.roles, role] : rule.roles.filter((r) => r !== role) })}
                    />
                  )}
                />
              ))}
            </Box>
            <Box sx={{ display: 'flex', gap: 2, flexWrap: 'wrap', alignItems: 'center', mt: 1 }}>
              <FormControlLabel label={t('confidentiality.step_up')} control={<Switch checked={rule.step_up} onChange={(e) => setRule(level, { step_up: e.target.checked })} />} />
              <FormControlLabel label={t('confidentiality.block_bulk_export')} control={<Switch checked={rule.block_bulk_export} onChange={(e) => setRule(level, { block_bulk_export: e.target.checked })} />} />
              <FormControl size="small" sx={{ minWidth: 220 }}>
                <InputLabel>{t('confidentiality.watermark')}</InputLabel>
                <Select label={t('confidentiality.watermark')} value={rule.watermark} onChange={(e) => setRule(level, { watermark: e.target.value })}>
                  {data.watermark_modes.map((m) => <MenuItem key={m} value={m}>{t(`confidentiality.modes.${m}`)}</MenuItem>)}
                </Select>
              </FormControl>
              <TextField
                size="small" type="number" label={t('confidentiality.max_downloads')} value={rule.max_downloads_per_hour ?? ''} sx={{ width: 200 }}
                onChange={(e) => setRule(level, { max_downloads_per_hour: e.target.value === '' ? null : Number(e.target.value) })}
              />
            </Box>
          </Box>
        );
      })}

      <Typography variant="subtitle1" fontWeight={600}>{t('confidentiality.step_up_title')}</Typography>
      <Typography variant="caption" color="text.secondary" display="block">{t('confidentiality.step_up_hint')}</Typography>
      <Box sx={{ display: 'flex', gap: 2, alignItems: 'center', mt: 1, flexWrap: 'wrap' }}>
        <TextField
          size="small" type="number" label={t('confidentiality.max_age')} value={config.step_up.max_age_seconds} sx={{ width: 240 }}
          onChange={(e) => setConfig({ ...config, step_up: { ...config.step_up, max_age_seconds: Number(e.target.value) } })}
        />
        <FormControlLabel
          label={t('confidentiality.admin_actions')}
          control={<Switch checked={config.step_up.admin_actions} onChange={(e) => setConfig({ ...config, step_up: { ...config.step_up, admin_actions: e.target.checked } })} />}
        />
      </Box>
      <Box sx={{ mt: 2 }}><Button variant="contained" onClick={save}>{t('common.save')}</Button></Box>
    </Paper>
  );
};
