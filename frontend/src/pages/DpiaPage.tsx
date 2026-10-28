import React, { useEffect, useState } from 'react';
import {
  Alert, Box, Button, Chip, Container, Dialog, DialogActions, DialogContent, DialogTitle,
  FormControl, FormControlLabel, InputLabel, MenuItem, Paper, Select, Switch, Table, TableBody,
  TableCell, TableHead, TableRow, TextField, Typography,
} from '@mui/material';
import PolicyIcon from '@mui/icons-material/Policy';
import AddIcon from '@mui/icons-material/Add';
import { PramaLayout } from '../components/PramaLayout';
import StatusNotesDialog from '../components/StatusNotesDialog';
import { apiClient } from '../services/apiClient';
import { AnnotationsButton } from '../components/AnnotationsPanel';
import { useTranslation } from '../i18n/useTranslation';
import { ErrorBanner } from '../components/ErrorBanner';

interface Dpia {
  id: string; row_version: number; code: string; title: string; status: string; screening_outcome: string | null;
  engine_suggestion: string | null; processing_activity_id: string | null;
  screening_rationale: string | null; description: string | null;
  necessity_proportionality: string | null; risks_text: string | null; measures_text: string | null;
  residual_risk_text: string | null; dpo_opinion: string | null; controller_decision: string | null;
  action_plan: string | null; next_review_date: string | null; confidentiality_level?: string;
  screening?: { criterion_code: string; label: string; answer: boolean | null; notes: string | null }[];
}
interface Processing { id: string; code: string; title: string }

const TRANSITIONS: Record<string, string[]> = {
  SCREENING: ['NOT_REQUIRED', 'IN_PROGRESS'],
  NOT_REQUIRED: ['SCREENING'],
  IN_PROGRESS: ['DPO_REVIEW', 'SCREENING'],
  DPO_REVIEW: ['AWAITING_DECISION', 'IN_PROGRESS'],
  AWAITING_DECISION: ['APPROVED', 'IN_PROGRESS'],
  APPROVED: ['TO_REVIEW', 'ARCHIVED'],
  TO_REVIEW: ['IN_PROGRESS', 'APPROVED'],
  ARCHIVED: [],
};

const empty = {
  title: '', processing_activity_id: '', screening_outcome: '', screening_rationale: '',
  description: '', necessity_proportionality: '', risks_text: '', measures_text: '',
  residual_risk_text: '', dpo_opinion: '', controller_decision: '', action_plan: '', next_review_date: '',
  confidentiality_level: 'INTERNAL',
};

export const DpiaPage: React.FC = () => {
  const { t } = useTranslation('privacypa');
  const [items, setItems] = useState<Dpia[]>([]);
  const [procs, setProcs] = useState<Processing[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<Dpia | null>(null);
  const [form, setForm] = useState(empty);
  const [screening, setScreening] = useState<NonNullable<Dpia['screening']>>([]);
  const [riskRows, setRiskRows] = useState<{ code: string; inherent_level: string; residual_level: string | null; risk_id: string }[]>([]);
  const [riskCatalog, setRiskCatalog] = useState<{ id: string; title: string }[]>([]);
  const [statusChange, setStatusChange] = useState<{ row: Dpia; next: string } | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);

  const load = async () => {
    try {
      const [d, p] = await Promise.all([
        apiClient.get<Dpia[]>('/api/privacypa/dpias'),
        apiClient.get<Processing[]>('/api/privacypa/processing-activities'),
      ]);
      setItems(d); setProcs(p);
    } catch (e: any) { setError(e.message); }
  };
  useEffect(() => { load(); }, []);

  const openEdit = async (row: Dpia) => {
    const full = await apiClient.get<Dpia>(`/api/privacypa/dpias/${row.id}`);
    setEditing(full);
    setForm({
      title: full.title, processing_activity_id: full.processing_activity_id || '',
      screening_outcome: full.screening_outcome || '', screening_rationale: full.screening_rationale || '',
      description: full.description || '', necessity_proportionality: full.necessity_proportionality || '',
      risks_text: full.risks_text || '', measures_text: full.measures_text || '',
      residual_risk_text: full.residual_risk_text || '', dpo_opinion: full.dpo_opinion || '',
      controller_decision: full.controller_decision || '', action_plan: full.action_plan || '',
      next_review_date: full.next_review_date || '',
      confidentiality_level: full.confidentiality_level || 'INTERNAL',
    });
    setScreening(full.screening || []);
    const [ras, catalog] = await Promise.all([
      apiClient.get<any[]>(`/api/privacypa/risk-assessments?entity_type=dpia&entity_id=${full.id}`),
      apiClient.get<{ id: string; title: string }[]>('/api/privacypa/risks'),
    ]);
    setRiskRows(ras);
    setRiskCatalog(catalog);
    setOpen(true);
  };

  const payload = () => ({
    title: form.title.trim(),
    processing_activity_id: form.processing_activity_id || null,
    screening_outcome: form.screening_outcome || null,
    screening_rationale: form.screening_rationale || null,
    description: form.description || null,
    necessity_proportionality: form.necessity_proportionality || null,
    risks_text: form.risks_text || null,
    measures_text: form.measures_text || null,
    residual_risk_text: form.residual_risk_text || null,
    dpo_opinion: form.dpo_opinion || null,
    controller_decision: form.controller_decision || null,
    action_plan: form.action_plan || null,
    next_review_date: form.next_review_date || null,
    confidentiality_level: form.confidentiality_level || null,
  });

  const save = async () => {
    if (!form.title.trim()) { setError(t('common.fill_required')); return; }
    try {
      let saved: Dpia;
      if (editing) {
        saved = await apiClient.patch<Dpia>(`/api/privacypa/dpias/${editing.id}`, payload(), { version: editing.row_version });
        // il PATCH ha incrementato la versione: lo screening usa quella restituita
        saved = await apiClient.put<Dpia>(`/api/privacypa/dpias/${editing.id}/screening`, {
          answers: screening.map((s) => ({ criterion_code: s.criterion_code, answer: s.answer, notes: s.notes })),
        }, { version: saved.row_version });
      } else {
        saved = await apiClient.post<Dpia>('/api/privacypa/dpias', payload());
      }
      setOpen(false); setEditing(null); await load();
      if (!editing && saved.id) await openEdit(saved);
    } catch (e: any) { setError(e.message); }
  };

  const changeStatus = async (id: string, next: string, version: number, notes?: string) => {
    try {
      await apiClient.post(`/api/privacypa/dpias/${id}/status`, { status: next, notes: notes || null }, { version });
      setStatusChange(null); setStatusError(null);
      await load();
    } catch (e: any) {
      if (statusChange) setStatusError(e.message); else setError(e.message);
    }
  };

  // Invio in attesa di decisione e decisioni passano da una nota (§165); le altre transizioni sono dirette.
  const requestStatusChange = (row: Dpia, next: string) => {
    if (row.status === 'AWAITING_DECISION' || next === 'AWAITING_DECISION' || next === 'APPROVED') {
      setStatusError(null);
      setStatusChange({ row, next });
    } else {
      changeStatus(row.id, next, row.row_version);
    }
  };

  return (
    <PramaLayout pageTitle={t('dpia.title')}>
      <Container maxWidth="lg">
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
          <PolicyIcon color="primary" />
          <Typography variant="h4" fontWeight={600}>{t('dpia.title')}</Typography>
          <Box sx={{ flex: 1 }} />
          <Button variant="contained" startIcon={<AddIcon />} onClick={() => { setEditing(null); setForm(empty); setScreening([]); setOpen(true); }}>{t('dpia.add')}</Button>
        </Box>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>{t('dpia.subtitle')}</Typography>
        <ErrorBanner message={error} onClose={() => setError(null)} />
        <Paper>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>{t('dpia.code')}</TableCell>
                <TableCell>{t('dpia.doc_title')}</TableCell>
                <TableCell>{t('dpia.status')}</TableCell>
                <TableCell>{t('dpia.suggestion')}</TableCell>
                <TableCell>{t('dpia.outcome')}</TableCell>
                <TableCell />
              </TableRow>
            </TableHead>
            <TableBody>
              {items.map((row) => (
                <TableRow key={row.id} hover>
                  <TableCell sx={{ fontFamily: 'monospace' }}>{row.code} <AnnotationsButton entityType="dpia" entityId={row.id} title={row.code} /></TableCell>
                  <TableCell><Button size="small" sx={{ textTransform: 'none' }} onClick={() => openEdit(row)}>{row.title}</Button></TableCell>
                  <TableCell><Chip size="small" label={t(`dpia.st.${row.status}`)} /></TableCell>
                  <TableCell>{row.engine_suggestion ? t(`dpia.out.${row.engine_suggestion}`) : '—'}</TableCell>
                  <TableCell>{row.screening_outcome ? t(`dpia.out.${row.screening_outcome}`) : '—'}</TableCell>
                  <TableCell>
                    {(TRANSITIONS[row.status] || []).map((n) => (
                      <Button key={n} size="small" sx={{ textTransform: 'none' }} onClick={() => requestStatusChange(row, n)}>{t(`dpia.st.${n}`)}</Button>
                    ))}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Paper>
      </Container>
      <Dialog open={open} onClose={() => setOpen(false)} maxWidth="md" fullWidth>
        <DialogTitle>{editing ? `${editing.code} — ${t('dpia.edit')}` : t('dpia.add')}</DialogTitle>
        <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 2 }}>
          <TextField label={t('dpia.doc_title')} value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} required />
          <FormControl fullWidth>
            <InputLabel>{t('register.fields.confidentiality')}</InputLabel>
            <Select label={t('register.fields.confidentiality')} value={form.confidentiality_level} onChange={(e) => setForm({ ...form, confidentiality_level: e.target.value })}>
              {['INTERNAL', 'RESTRICTED', 'HIGHLY_RESTRICTED'].map((x) => <MenuItem key={x} value={x}>{t(`register.confidentiality.${x}`)}</MenuItem>)}
            </Select>
          </FormControl>
          <FormControl fullWidth>
            <InputLabel>{t('dpia.processing')}</InputLabel>
            <Select label={t('dpia.processing')} value={form.processing_activity_id} onChange={(e) => setForm({ ...form, processing_activity_id: e.target.value })}>
              <MenuItem value="">—</MenuItem>
              {procs.map((p) => <MenuItem key={p.id} value={p.id}>{p.code} — {p.title}</MenuItem>)}
            </Select>
          </FormControl>
          {editing && (
            <>
              <Alert severity="info">{t('dpia.engine_hint')} <strong>{editing.engine_suggestion ? t(`dpia.out.${editing.engine_suggestion}`) : '—'}</strong></Alert>
              {screening.map((s, i) => (
                <Box key={s.criterion_code} sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
                  <FormControlLabel
                    control={<Switch checked={s.answer === true} onChange={(e) => {
                      const next = [...screening];
                      next[i] = { ...s, answer: e.target.checked };
                      setScreening(next);
                    }} />}
                    label={s.label}
                    sx={{ flex: 1 }}
                  />
                </Box>
              ))}
              <FormControl fullWidth>
                <InputLabel>{t('dpia.outcome')}</InputLabel>
                <Select label={t('dpia.outcome')} value={form.screening_outcome} onChange={(e) => setForm({ ...form, screening_outcome: e.target.value })}>
                  <MenuItem value="">—</MenuItem>
                  {['PROCEED', 'NOT_REQUIRED', 'UNCERTAIN'].map((x) => <MenuItem key={x} value={x}>{t(`dpia.out.${x}`)}</MenuItem>)}
                </Select>
              </FormControl>
              <TextField label={t('dpia.rationale')} value={form.screening_rationale} onChange={(e) => setForm({ ...form, screening_rationale: e.target.value })} multiline minRows={2} />
              <TextField label={t('dpia.description')} value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} multiline minRows={2} />
              <TextField label={t('dpia.necessity')} value={form.necessity_proportionality} onChange={(e) => setForm({ ...form, necessity_proportionality: e.target.value })} multiline minRows={2} />
              <TextField label={t('dpia.risks')} value={form.risks_text} onChange={(e) => setForm({ ...form, risks_text: e.target.value })} multiline minRows={2} />
              <TextField label={t('dpia.measures')} value={form.measures_text} onChange={(e) => setForm({ ...form, measures_text: e.target.value })} multiline minRows={2} />
              <TextField label={t('dpia.residual')} value={form.residual_risk_text} onChange={(e) => setForm({ ...form, residual_risk_text: e.target.value })} multiline minRows={2} />
              <TextField label={t('dpia.dpo')} value={form.dpo_opinion} onChange={(e) => setForm({ ...form, dpo_opinion: e.target.value })} multiline minRows={2} />
              <TextField label={t('dpia.decision')} value={form.controller_decision} onChange={(e) => setForm({ ...form, controller_decision: e.target.value })} multiline minRows={2} />
              <TextField label={t('dpia.actions')} value={form.action_plan} onChange={(e) => setForm({ ...form, action_plan: e.target.value })} multiline minRows={2} />
              <TextField type="date" label={t('dpia.next_review')} InputLabelProps={{ shrink: true }} value={form.next_review_date} onChange={(e) => setForm({ ...form, next_review_date: e.target.value })} />
              <Typography variant="subtitle2">{t('dpia.linked_risks')}</Typography>
              {riskRows.length === 0 && <Typography variant="body2" color="text.secondary">{t('dpia.no_risks')}</Typography>}
              {riskRows.map((r) => (
                <Chip key={r.code} sx={{ mr: 0.5, mb: 0.5 }} label={`${r.code} ${riskCatalog.find((c) => c.id === r.risk_id)?.title || ''} · ${r.inherent_level}${r.residual_level ? ' → ' + r.residual_level : ''}`} />
              ))}
            </>
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setOpen(false)}>{t('common.cancel')}</Button>
          <Button variant="contained" onClick={save}>{t('common.save')}</Button>
        </DialogActions>
      </Dialog>
      <StatusNotesDialog
        open={Boolean(statusChange)}
        title={statusChange ? t(statusChange.next === 'AWAITING_DECISION' ? 'status_notes.submit_title' : statusChange.next === 'APPROVED' ? 'status_notes.approve_title' : 'status_notes.back_title') : ''}
        hint={statusChange ? t(statusChange.next === 'AWAITING_DECISION' ? 'status_notes.submit_hint' : statusChange.next === 'APPROVED' ? 'status_notes.approve_hint' : 'status_notes.back_hint') : undefined}
        error={statusError}
        onConfirm={(notes) => statusChange && changeStatus(statusChange.row.id, statusChange.next, statusChange.row.row_version, notes)}
        onClose={() => { setStatusChange(null); setStatusError(null); }}
      />
    </PramaLayout>
  );
};
export default DpiaPage;
