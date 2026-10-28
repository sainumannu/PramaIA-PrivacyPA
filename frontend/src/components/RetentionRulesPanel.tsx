import React, { useEffect, useState } from 'react';
import {
  Alert, Box, Button, Chip, Dialog, DialogActions, DialogContent, DialogTitle, FormControl, IconButton,
  InputLabel, MenuItem, Select, TextField, Tooltip, Typography,
} from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import DeleteIcon from '@mui/icons-material/Delete';
import EditIcon from '@mui/icons-material/Edit';
import { apiClient } from '../services/apiClient';
import { useTranslation } from '../i18n/useTranslation';
import { ErrorBanner } from './ErrorBanner';

interface Rule {
  id: string;
  row_version: number;
  data_category_id: string | null;
  record_class: string | null;
  retention_value: number | null;
  retention_unit: string;
  retention_criterion: string;
  start_trigger: string | null;
  legal_reference: string | null;
  legal_reference_id: string | null;
  disposal_method: string;
  notes: string | null;
  period_text: string;
  disposal_text: string;
}

interface Props {
  processingId: string;
  categories: { id: string; name: string }[];
  canEdit: boolean;
  // Ogni modifica cambia la versione del trattamento: il chiamante la rilegge.
  onChanged?: () => void;
}

const UNITS = ['DAYS', 'MONTHS', 'YEARS', 'EVENT_BASED', 'PERMANENT'];
const DISPOSALS = ['DELETION', 'ANONYMIZATION', 'ARCHIVAL', 'TRANSFER_TO_ARCHIVE', 'OTHER'];
const TIMED = ['DAYS', 'MONTHS', 'YEARS'];
const empty = {
  data_category_id: '', record_class: '', retention_value: '', retention_unit: 'YEARS', retention_criterion: '',
  start_trigger: '', legal_reference: '', disposal_method: 'DELETION', notes: '',
};

// Regole di conservazione del trattamento (§21): una per categoria di dati o per l'intero trattamento.
export const RetentionRulesPanel: React.FC<Props> = ({ processingId, categories, canEdit, onChanged }) => {
  const { t } = useTranslation('privacypa');
  const [rules, setRules] = useState<Rule[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [dialogError, setDialogError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<Rule | null>(null);
  const [form, setForm] = useState(empty);

  const base = `/api/privacypa/processing-activities/${processingId}/retention-rules`;
  const load = async () => {
    try {
      setRules(await apiClient.get<Rule[]>(base));
    } catch (err: any) {
      setError(err.message);
    }
  };
  useEffect(() => { load(); }, [processingId]);

  const openDialog = (rule: Rule | null) => {
    setEditing(rule);
    setDialogError(null);
    setForm(rule ? {
      data_category_id: rule.data_category_id || '',
      record_class: rule.record_class || '',
      retention_value: rule.retention_value != null ? String(rule.retention_value) : '',
      retention_unit: rule.retention_unit,
      retention_criterion: rule.retention_criterion,
      start_trigger: rule.start_trigger || '',
      legal_reference: rule.legal_reference || '',
      disposal_method: rule.disposal_method,
      notes: rule.notes || '',
    } : empty);
    setOpen(true);
  };

  const save = async () => {
    const body = {
      data_category_id: form.data_category_id || null,
      record_class: form.record_class.trim() || null,
      retention_value: TIMED.includes(form.retention_unit) && form.retention_value ? Number(form.retention_value) : null,
      retention_unit: form.retention_unit,
      retention_criterion: form.retention_criterion.trim(),
      start_trigger: form.start_trigger.trim() || null,
      legal_reference: form.legal_reference.trim() || null,
      // Il PATCH sostituisce la regola: il riferimento normativo collegato (impostato via API) si rimanda com'era.
      legal_reference_id: editing?.legal_reference_id ?? null,
      disposal_method: form.disposal_method,
      notes: form.notes.trim() || null,
    };
    try {
      if (editing) {
        await apiClient.patch(`/api/privacypa/retention-rules/${editing.id}`, body, { version: editing.row_version });
      } else {
        await apiClient.post(base, body);
      }
      setOpen(false);
      await load();
      onChanged?.();
    } catch (err: any) {
      setDialogError(err.message);
    }
  };

  const remove = async (rule: Rule) => {
    if (!window.confirm(t('retention.confirm_delete'))) return;
    try {
      await apiClient.delete(`/api/privacypa/retention-rules/${rule.id}`);
      await load();
      onChanged?.();
    } catch (err: any) {
      setError(err.message);
    }
  };

  const categoryName = (id: string | null) => categories.find((c) => c.id === id)?.name;

  return (
    <Box sx={{ border: 1, borderColor: 'divider', borderRadius: 1, p: 1.5 }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
        <Typography variant="subtitle2" sx={{ flex: 1 }}>{t('retention.title')}</Typography>
        {canEdit && (
          <Button size="small" startIcon={<AddIcon />} onClick={() => openDialog(null)}>{t('retention.add')}</Button>
        )}
      </Box>
      <ErrorBanner message={error} onClose={() => setError(null)} />
      {rules.length === 0 && <Typography variant="body2" color="text.secondary">{t('retention.empty')}</Typography>}
      {rules.map((r) => (
        <Box key={r.id} sx={{ display: 'flex', alignItems: 'center', gap: 1, py: 0.75, borderTop: 1, borderColor: 'divider' }}>
          <Box sx={{ flex: 1 }}>
            <Typography variant="body2">
              <strong>{categoryName(r.data_category_id) || r.record_class || t('retention.whole_processing')}</strong>
              {' — '}{r.period_text}{' · '}{t(`retention.disposal.${r.disposal_method}`)}
            </Typography>
            <Typography variant="caption" color="text.secondary">
              {r.retention_criterion}{r.legal_reference ? ` (${r.legal_reference})` : ''}
            </Typography>
          </Box>
          <Chip size="small" variant="outlined" label={t(`retention.unit.${r.retention_unit}`)} />
          {canEdit && (
            <>
              <Tooltip title={t('common.edit')}><IconButton size="small" onClick={() => openDialog(r)}><EditIcon fontSize="small" /></IconButton></Tooltip>
              <Tooltip title={t('retention.delete')}><IconButton size="small" onClick={() => remove(r)}><DeleteIcon fontSize="small" /></IconButton></Tooltip>
            </>
          )}
        </Box>
      ))}

      <Dialog open={open} onClose={() => setOpen(false)} maxWidth="sm" fullWidth>
        <DialogTitle>{editing ? t('retention.edit') : t('retention.add')}</DialogTitle>
        <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 2 }}>
          {dialogError && <Alert severity="error">{dialogError}</Alert>}
          <FormControl fullWidth>
            <InputLabel>{t('retention.category')}</InputLabel>
            <Select label={t('retention.category')} value={form.data_category_id} onChange={(e) => setForm({ ...form, data_category_id: e.target.value })}>
              <MenuItem value="">{t('retention.whole_processing')}</MenuItem>
              {categories.map((c) => <MenuItem key={c.id} value={c.id}>{c.name}</MenuItem>)}
            </Select>
          </FormControl>
          <TextField label={t('retention.record_class')} value={form.record_class} onChange={(e) => setForm({ ...form, record_class: e.target.value })} />
          <Box sx={{ display: 'flex', gap: 2 }}>
            <FormControl fullWidth>
              <InputLabel>{t('retention.unit_label')}</InputLabel>
              <Select label={t('retention.unit_label')} value={form.retention_unit} onChange={(e) => setForm({ ...form, retention_unit: e.target.value })}>
                {UNITS.map((u) => <MenuItem key={u} value={u}>{t(`retention.unit.${u}`)}</MenuItem>)}
              </Select>
            </FormControl>
            {TIMED.includes(form.retention_unit) && (
              <TextField
                type="number" label={t('retention.value')} value={form.retention_value} required
                onChange={(e) => setForm({ ...form, retention_value: e.target.value })} inputProps={{ min: 1 }}
              />
            )}
          </Box>
          <TextField
            label={t('retention.trigger')} value={form.start_trigger} required={form.retention_unit === 'EVENT_BASED'}
            helperText={t('retention.trigger_hint')} onChange={(e) => setForm({ ...form, start_trigger: e.target.value })}
          />
          <TextField label={t('retention.criterion')} value={form.retention_criterion} required multiline minRows={2} onChange={(e) => setForm({ ...form, retention_criterion: e.target.value })} />
          <TextField label={t('retention.legal_reference')} value={form.legal_reference} onChange={(e) => setForm({ ...form, legal_reference: e.target.value })} />
          <FormControl fullWidth>
            <InputLabel>{t('retention.disposal_label')}</InputLabel>
            <Select label={t('retention.disposal_label')} value={form.disposal_method} onChange={(e) => setForm({ ...form, disposal_method: e.target.value })}>
              {DISPOSALS.map((d) => <MenuItem key={d} value={d}>{t(`retention.disposal.${d}`)}</MenuItem>)}
            </Select>
          </FormControl>
          <TextField label={t('retention.notes')} value={form.notes} multiline minRows={2} onChange={(e) => setForm({ ...form, notes: e.target.value })} />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setOpen(false)}>{t('common.cancel')}</Button>
          <Button variant="contained" onClick={save} disabled={!form.retention_criterion.trim()}>{t('common.save')}</Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
};

export default RetentionRulesPanel;
