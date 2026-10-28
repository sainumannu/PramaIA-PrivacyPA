import React, { useEffect, useState } from 'react';
import {
  Box, Button, Chip, Container, Dialog, DialogActions, DialogContent, DialogTitle,
  FormControl, FormControlLabel, InputLabel, MenuItem, Paper, Select, Switch, Table, TableBody,
  TableCell, TableHead, TableRow, TextField, Typography,
} from '@mui/material';
import PublicIcon from '@mui/icons-material/Public';
import AddIcon from '@mui/icons-material/Add';
import { PramaLayout } from '../components/PramaLayout';
import { apiClient } from '../services/apiClient';
import { useTranslation } from '../i18n/useTranslation';
import { ErrorBanner } from '../components/ErrorBanner';
import { SinglePick } from '../components/UnitPicker';

interface Transfer {
  id: string; row_version: number; code: string; title: string; country_code: string; mechanism: string;
  tia_required: boolean; tia_outcome: string; status: string;
  importer_party_id: string | null; description: string | null; scc_module: string | null;
  tia_rationale: string | null; law_access_assessment: string | null;
  supplementary_measures: string | null; next_review_date: string | null;
  adequacy_reference: string | null;
  exporter_party_id: string | null; tia_document_id: string | null; last_review_date: string | null;
  links?: { entity_type: string; entity_id: string }[];
}
interface Party { id: string; code: string; legal_name: string }
interface Processing { id: string; code: string; title: string }

const empty = {
  title: '', country_code: 'US', importer_party_id: '', mechanism: 'SCC', scc_module: '2',
  tia_required: true, tia_outcome: 'PENDING', tia_rationale: '', law_access_assessment: '',
  supplementary_measures: '', description: '', status: 'DRAFT', next_review_date: '',
  adequacy_reference: '', processing_id: '',
};

export const TransfersPage: React.FC = () => {
  const { t } = useTranslation('privacypa');
  const [items, setItems] = useState<Transfer[]>([]);
  const [parties, setParties] = useState<Party[]>([]);
  const [procs, setProcs] = useState<Processing[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<Transfer | null>(null);
  const [form, setForm] = useState(empty);

  const load = async () => {
    try {
      const [tr, pa, pr] = await Promise.all([
        apiClient.get<Transfer[]>('/api/privacypa/transfers'),
        apiClient.get<Party[]>('/api/privacypa/parties'),
        apiClient.get<Processing[]>('/api/privacypa/processing-activities'),
      ]);
      setItems(tr); setParties(pa); setProcs(pr);
    } catch (e: any) { setError(e.message); }
  };
  useEffect(() => { load(); }, []);

  // Il PATCH sostituisce il record e i collegamenti: in modifica i campi che il modulo non mostra
  // e gli altri collegamenti (oltre al trattamento scelto qui) si rimandano com'erano.
  const links = () => {
    const shown = editing?.links?.find((l) => l.entity_type === 'processing_activity');
    const others = (editing?.links || [])
      .filter((l) => l !== shown && !(l.entity_type === 'processing_activity' && l.entity_id === form.processing_id))
      .map(({ entity_type, entity_id }) => ({ entity_type, entity_id }));
    return form.processing_id ? [{ entity_type: 'processing_activity', entity_id: form.processing_id }, ...others] : others;
  };

  const payload = () => ({
    title: form.title.trim(),
    country_code: form.country_code.toUpperCase(),
    importer_party_id: form.importer_party_id || null,
    mechanism: form.mechanism,
    scc_module: form.scc_module || null,
    tia_required: form.tia_required,
    tia_outcome: form.tia_outcome,
    tia_rationale: form.tia_rationale || null,
    law_access_assessment: form.law_access_assessment || null,
    supplementary_measures: form.supplementary_measures || null,
    description: form.description || null,
    status: form.status,
    next_review_date: form.next_review_date || null,
    adequacy_reference: form.adequacy_reference || null,
    exporter_party_id: editing?.exporter_party_id ?? null,
    tia_document_id: editing?.tia_document_id ?? null,
    last_review_date: editing?.last_review_date ?? null,
    links: links(),
  });

  const save = async () => {
    if (!form.title.trim()) { setError(t('common.fill_required')); return; }
    try {
      if (editing) await apiClient.patch(`/api/privacypa/transfers/${editing.id}`, payload(), { version: editing.row_version });
      else await apiClient.post('/api/privacypa/transfers', payload());
      setOpen(false); await load();
    } catch (e: any) { setError(e.message); }
  };

  return (
    <PramaLayout pageTitle={t('tia.title')}>
      <Container maxWidth="lg">
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
          <PublicIcon color="primary" />
          <Typography variant="h4" fontWeight={600}>{t('tia.title')}</Typography>
          <Box sx={{ flex: 1 }} />
          <Button variant="contained" startIcon={<AddIcon />} onClick={() => { setEditing(null); setForm(empty); setOpen(true); }}>{t('tia.add')}</Button>
        </Box>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>{t('tia.subtitle')}</Typography>
        <ErrorBanner message={error} onClose={() => setError(null)} />
        <Paper>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>{t('tia.code')}</TableCell>
                <TableCell>{t('tia.doc_title')}</TableCell>
                <TableCell>{t('tia.country')}</TableCell>
                <TableCell>{t('tia.mechanism')}</TableCell>
                <TableCell>TIA</TableCell>
                <TableCell>{t('tia.status')}</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {items.map((row) => (
                <TableRow key={row.id} hover sx={{ cursor: 'pointer' }} onClick={async () => {
                  const full = await apiClient.get<Transfer>(`/api/privacypa/transfers/${row.id}`);
                  setEditing(full);
                  setForm({
                    title: full.title, country_code: full.country_code, importer_party_id: full.importer_party_id || '',
                    mechanism: full.mechanism, scc_module: full.scc_module || '', tia_required: full.tia_required,
                    tia_outcome: full.tia_outcome, tia_rationale: full.tia_rationale || '',
                    law_access_assessment: full.law_access_assessment || '', supplementary_measures: full.supplementary_measures || '',
                    description: full.description || '', status: full.status, next_review_date: full.next_review_date || '',
                    adequacy_reference: full.adequacy_reference || '',
                    processing_id: full.links?.find((l) => l.entity_type === 'processing_activity')?.entity_id || '',
                  });
                  setOpen(true);
                }}>
                  <TableCell sx={{ fontFamily: 'monospace' }}>{row.code}</TableCell>
                  <TableCell>{row.title}</TableCell>
                  <TableCell>{row.country_code}</TableCell>
                  <TableCell>{t(`tia.mech.${row.mechanism}`)}</TableCell>
                  <TableCell>
                    <Chip size="small" color={row.tia_required && row.tia_outcome === 'PENDING' ? 'warning' : 'default'} label={t(`tia.out.${row.tia_outcome}`)} />
                  </TableCell>
                  <TableCell>{t(`tia.st.${row.status}`)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Paper>
      </Container>
      <Dialog open={open} onClose={() => setOpen(false)} maxWidth="md" fullWidth>
        <DialogTitle>{editing ? `${editing.code}` : t('tia.add')}</DialogTitle>
        <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 2 }}>
          <TextField label={t('tia.doc_title')} value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} required />
          <TextField label={t('tia.country')} value={form.country_code} onChange={(e) => setForm({ ...form, country_code: e.target.value })} inputProps={{ maxLength: 2 }} />
          <SinglePick
            label={t('tia.importer')} items={parties.map((p) => ({ id: p.id, label: p.legal_name }))}
            value={form.importer_party_id} onChange={(id) => setForm((f) => ({ ...f, importer_party_id: id }))}
          />
          <FormControl fullWidth>
            <InputLabel>{t('tia.processing')}</InputLabel>
            <Select label={t('tia.processing')} value={form.processing_id} onChange={(e) => setForm({ ...form, processing_id: e.target.value })}>
              <MenuItem value="">—</MenuItem>
              {procs.map((p) => <MenuItem key={p.id} value={p.id}>{p.code} — {p.title}</MenuItem>)}
            </Select>
          </FormControl>
          <FormControl fullWidth>
            <InputLabel>{t('tia.mechanism')}</InputLabel>
            <Select label={t('tia.mechanism')} value={form.mechanism} onChange={(e) => setForm({ ...form, mechanism: e.target.value })}>
              {['ADEQUACY', 'SCC', 'BCR', 'DEROGATION_49', 'OTHER'].map((x) => <MenuItem key={x} value={x}>{t(`tia.mech.${x}`)}</MenuItem>)}
            </Select>
          </FormControl>
          <TextField label={t('tia.scc')} value={form.scc_module} onChange={(e) => setForm({ ...form, scc_module: e.target.value })} />
          <FormControlLabel control={<Switch checked={form.tia_required} onChange={(e) => setForm({ ...form, tia_required: e.target.checked })} />} label={t('tia.required')} />
          <FormControl fullWidth>
            <InputLabel>{t('tia.outcome')}</InputLabel>
            <Select label={t('tia.outcome')} value={form.tia_outcome} onChange={(e) => setForm({ ...form, tia_outcome: e.target.value })}>
              {['PENDING', 'NOT_REQUIRED', 'ACCEPTABLE', 'ACCEPTABLE_WITH_MEASURES', 'NOT_ACCEPTABLE'].map((x) => (
                <MenuItem key={x} value={x}>{t(`tia.out.${x}`)}</MenuItem>
              ))}
            </Select>
          </FormControl>
          <TextField label={t('tia.rationale')} value={form.tia_rationale} onChange={(e) => setForm({ ...form, tia_rationale: e.target.value })} multiline minRows={2} />
          <TextField label={t('tia.law_access')} value={form.law_access_assessment} onChange={(e) => setForm({ ...form, law_access_assessment: e.target.value })} multiline minRows={2} />
          <TextField label={t('tia.measures')} value={form.supplementary_measures} onChange={(e) => setForm({ ...form, supplementary_measures: e.target.value })} multiline minRows={2} />
          <FormControl fullWidth>
            <InputLabel>{t('tia.status')}</InputLabel>
            <Select label={t('tia.status')} value={form.status} onChange={(e) => setForm({ ...form, status: e.target.value })}>
              {['DRAFT', 'ACTIVE', 'TO_REVIEW', 'SUSPENDED', 'ENDED'].map((x) => <MenuItem key={x} value={x}>{t(`tia.st.${x}`)}</MenuItem>)}
            </Select>
          </FormControl>
          <TextField type="date" label={t('tia.next_review')} InputLabelProps={{ shrink: true }} value={form.next_review_date} onChange={(e) => setForm({ ...form, next_review_date: e.target.value })} />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setOpen(false)}>{t('common.cancel')}</Button>
          <Button variant="contained" onClick={save}>{t('common.save')}</Button>
        </DialogActions>
      </Dialog>
    </PramaLayout>
  );
};
export default TransfersPage;
