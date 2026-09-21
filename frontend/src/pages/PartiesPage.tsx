import React, { useEffect, useState } from 'react';
import {
  Alert, Box, Button, Chip, Container, Dialog, DialogActions, DialogContent, DialogTitle,
  FormControl, FormControlLabel, InputLabel, MenuItem, Paper, Select, Switch, Tab, Tabs,
  Table, TableBody, TableCell, TableHead, TableRow, TextField, Typography,
} from '@mui/material';
import BusinessIcon from '@mui/icons-material/Business';
import AddIcon from '@mui/icons-material/Add';
import { PramaLayout } from '../components/PramaLayout';
import { apiClient } from '../services/apiClient';
import { AnnotationsButton } from '../components/AnnotationsPanel';
import { useTranslation } from '../i18n/useTranslation';

interface Party {
  id: string; row_version: number; code: string; legal_name: string; party_type: string; pec: string | null;
  email: string | null; country_code: string | null; tax_id: string | null;
  processor_profile?: { status: string; next_assessment_date: string | null };
}
interface Agreement {
  id: string; party_id: string; status: string; expiry_date: string | null;
  instructions_present: boolean | null; security_clause: boolean | null;
  subprocessor_clause: boolean | null; audit_clause: boolean | null;
}

const TYPES = ['PROCESSOR','JOINT_CONTROLLER','RECIPIENT','SUPPLIER','PUBLIC_BODY','OTHER'];
const emptyParty = { legal_name: '', party_type: 'PROCESSOR', pec: '', email: '', country_code: 'IT', tax_id: '', notes: '' };
const emptyAgr = {
  party_id: '', status: 'DRAFT', expiry_date: '',
  instructions_present: false, confidentiality_clause: false, security_clause: false,
  subprocessor_clause: false, rights_assistance_clause: false, breach_assistance_clause: false,
  dpia_assistance_clause: false, deletion_return_clause: false, audit_clause: false,
};

export const PartiesPage: React.FC = () => {
  const { t } = useTranslation('privacypa');
  const [tab, setTab] = useState(0);
  const [items, setItems] = useState<Party[]>([]);
  const [agreements, setAgreements] = useState<Agreement[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [agrOpen, setAgrOpen] = useState(false);
  const [editing, setEditing] = useState<Party | null>(null);
  const [form, setForm] = useState(emptyParty);
  const [agr, setAgr] = useState(emptyAgr);

  const load = async () => {
    try {
      const [p, a] = await Promise.all([
        apiClient.get<Party[]>('/api/privacypa/parties'),
        apiClient.get<Agreement[]>('/api/privacypa/parties/agreements'),
      ]);
      setItems(p); setAgreements(a);
    } catch (e: any) { setError(e.message); }
  };
  useEffect(() => { load(); }, []);

  const saveParty = async () => {
    if (!form.legal_name.trim()) return;
    const body = {
      legal_name: form.legal_name, party_type: form.party_type, pec: form.pec || null,
      email: form.email || null, country_code: form.country_code || null, tax_id: form.tax_id || null,
      notes: form.notes || null,
      processor_profile: form.party_type === 'PROCESSOR' ? { status: 'TO_REVIEW' } : null,
    };
    try {
      if (editing) await apiClient.patch(`/api/privacypa/parties/${editing.id}`, body, { version: editing.row_version });
      else await apiClient.post('/api/privacypa/parties', body);
      setOpen(false); await load();
    } catch (e: any) { setError(e.message); }
  };

  const saveAgr = async () => {
    if (!agr.party_id) return;
    try {
      await apiClient.post('/api/privacypa/parties/agreements', {
        ...agr, expiry_date: agr.expiry_date || null,
      });
      setAgrOpen(false); await load();
    } catch (e: any) { setError(e.message); }
  };

  const partyName = (id: string) => items.find((p) => p.id === id)?.legal_name || id;

  return (
    <PramaLayout pageTitle={t('parties.title')}>
      <Container maxWidth="lg">
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
          <BusinessIcon color="primary" />
          <Typography variant="h4" fontWeight={600}>{t('parties.title')}</Typography>
        </Box>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>{t('parties.subtitle')}</Typography>
        {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>{error}</Alert>}
        <Paper>
          <Tabs value={tab} onChange={(_, v) => setTab(v)} sx={{ borderBottom: 1, borderColor: 'divider', '& .MuiTab-root': { textTransform: 'none' } }}>
            <Tab label={t('parties.tabs.registry')} />
            <Tab label={t('parties.tabs.agreements')} />
          </Tabs>
          {tab === 0 && (
            <Box sx={{ p: 2 }}>
              <Button variant="contained" startIcon={<AddIcon />} sx={{ mb: 2 }} onClick={() => { setEditing(null); setForm(emptyParty); setOpen(true); }}>{t('parties.add')}</Button>
              <Table size="small">
                <TableHead><TableRow>
                  <TableCell>{t('parties.code')}</TableCell>
                  <TableCell>{t('parties.name')}</TableCell>
                  <TableCell>{t('parties.type')}</TableCell>
                  <TableCell>PEC</TableCell>
                </TableRow></TableHead>
                <TableBody>
                  {items.map((row) => (
                    <TableRow key={row.id} hover sx={{ cursor: 'pointer' }} onClick={() => {
                      setEditing(row);
                      setForm({ legal_name: row.legal_name, party_type: row.party_type, pec: row.pec || '', email: row.email || '', country_code: row.country_code || 'IT', tax_id: row.tax_id || '', notes: '' });
                      setOpen(true);
                    }}>
                      <TableCell sx={{ fontFamily: 'monospace' }}>{row.code} <AnnotationsButton entityType="party" entityId={row.id} title={row.code} /></TableCell>
                      <TableCell>{row.legal_name}</TableCell>
                      <TableCell><Chip size="small" label={t(`parties.types.${row.party_type}`)} /></TableCell>
                      <TableCell>{row.pec || '—'}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </Box>
          )}
          {tab === 1 && (
            <Box sx={{ p: 2 }}>
              <Button variant="contained" startIcon={<AddIcon />} sx={{ mb: 2 }} onClick={() => { setAgr(emptyAgr); setAgrOpen(true); }}>{t('parties.add_agreement')}</Button>
              <Table size="small">
                <TableHead><TableRow>
                  <TableCell>{t('parties.name')}</TableCell>
                  <TableCell>{t('parties.agr_status')}</TableCell>
                  <TableCell>{t('parties.expiry')}</TableCell>
                  <TableCell>{t('parties.checklist')}</TableCell>
                </TableRow></TableHead>
                <TableBody>
                  {agreements.map((row) => (
                    <TableRow key={row.id}>
                      <TableCell>{partyName(row.party_id)}</TableCell>
                      <TableCell><Chip size="small" label={t(`parties.agr.${row.status}`)} /></TableCell>
                      <TableCell>{row.expiry_date || '—'}</TableCell>
                      <TableCell>
                        {[row.instructions_present, row.security_clause, row.subprocessor_clause, row.audit_clause].filter(Boolean).length}/4
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </Box>
          )}
        </Paper>
      </Container>

      <Dialog open={open} onClose={() => setOpen(false)} maxWidth="sm" fullWidth>
        <DialogTitle>{editing ? editing.code : t('parties.add')}</DialogTitle>
        <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 2 }}>
          <TextField label={t('parties.name')} value={form.legal_name} onChange={(e) => setForm({ ...form, legal_name: e.target.value })} required />
          <FormControl><InputLabel>{t('parties.type')}</InputLabel>
            <Select label={t('parties.type')} value={form.party_type} onChange={(e) => setForm({ ...form, party_type: e.target.value })}>
              {TYPES.map((x) => <MenuItem key={x} value={x}>{t(`parties.types.${x}`)}</MenuItem>)}
            </Select>
          </FormControl>
          <TextField label="PEC" value={form.pec} onChange={(e) => setForm({ ...form, pec: e.target.value })} />
          <TextField label="Email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} />
          <TextField label={t('parties.tax_id')} value={form.tax_id} onChange={(e) => setForm({ ...form, tax_id: e.target.value })} />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setOpen(false)}>{t('common.cancel')}</Button>
          <Button variant="contained" onClick={saveParty}>{t('common.save')}</Button>
        </DialogActions>
      </Dialog>

      <Dialog open={agrOpen} onClose={() => setAgrOpen(false)} maxWidth="sm" fullWidth>
        <DialogTitle>{t('parties.add_agreement')}</DialogTitle>
        <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 2 }}>
          <FormControl><InputLabel>{t('parties.name')}</InputLabel>
            <Select label={t('parties.name')} value={agr.party_id} onChange={(e) => setAgr({ ...agr, party_id: e.target.value })}>
              {items.map((p) => <MenuItem key={p.id} value={p.id}>{p.legal_name}</MenuItem>)}
            </Select>
          </FormControl>
          <TextField type="date" label={t('parties.expiry')} InputLabelProps={{ shrink: true }} value={agr.expiry_date} onChange={(e) => setAgr({ ...agr, expiry_date: e.target.value })} />
          {(['instructions_present','confidentiality_clause','security_clause','subprocessor_clause','rights_assistance_clause','breach_assistance_clause','dpia_assistance_clause','deletion_return_clause','audit_clause'] as const).map((k) => (
            <FormControlLabel key={k} control={<Switch checked={Boolean(agr[k])} onChange={(e) => setAgr({ ...agr, [k]: e.target.checked })} />} label={t(`parties.clauses.${k}`)} />
          ))}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setAgrOpen(false)}>{t('common.cancel')}</Button>
          <Button variant="contained" onClick={saveAgr}>{t('common.save')}</Button>
        </DialogActions>
      </Dialog>
    </PramaLayout>
  );
};
export default PartiesPage;
