import React, { useEffect, useMemo, useState } from 'react';
import {
  Alert, Box, Button, Chip, Container, Dialog, DialogActions, DialogContent, DialogTitle,
  FormControlLabel, Paper, Switch, Tab, Tabs, Table, TableBody, TableCell, TableHead, TableRow, TextField, Typography,
} from '@mui/material';
import BusinessIcon from '@mui/icons-material/Business';
import AddIcon from '@mui/icons-material/Add';
import PersonAddIcon from '@mui/icons-material/PersonAdd';
import { PramaLayout } from '../components/PramaLayout';
import { apiClient } from '../services/apiClient';
import { usePermissions } from '../services/permissions';
import { AnnotationsButton } from '../components/AnnotationsPanel';
import { useTranslation } from '../i18n/useTranslation';
import { ErrorBanner } from '../components/ErrorBanner';
import { SinglePick } from '../components/UnitPicker';
import {
  APPOINTED_TYPES, AppointmentChip, PartyDialog, PartyRef, hasValidAgreement, isArchived,
} from '../components/PartyPicker';

interface Agreement {
  id: string; party_id: string; status: string; effective_from: string | null; expiry_date: string | null;
  instructions_present: boolean | null; security_clause: boolean | null;
  subprocessor_clause: boolean | null; audit_clause: boolean | null;
}

const emptyAgr = {
  party_id: '', status: 'DRAFT', expiry_date: '',
  instructions_present: false, confidentiality_clause: false, security_clause: false,
  subprocessor_clause: false, rights_assistance_clause: false, breach_assistance_clause: false,
  dpia_assistance_clause: false, deletion_return_clause: false, audit_clause: false,
};

const ids = (p: PartyRef) => [p.tax_id, p.vat_id && p.vat_id !== p.tax_id ? p.vat_id : null].filter(Boolean).join(' · ');

// Anagrafica dei soggetti esterni: enti, società e persone fisiche, identificati dal codice fiscale.
// Responsabili e contitolari hanno un incarico a termine; alla scadenza o alla cessazione passano in archivio.
export const PartiesPage: React.FC = () => {
  const { t } = useTranslation('privacypa');
  const { can } = usePermissions();
  const [tab, setTab] = useState(0);
  const [items, setItems] = useState<PartyRef[]>([]);
  const [agreements, setAgreements] = useState<Agreement[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [dialog, setDialog] = useState<{ party: PartyRef | null; natural: boolean } | null>(null);
  const [agrOpen, setAgrOpen] = useState(false);
  const [agr, setAgr] = useState(emptyAgr);
  const [ceasing, setCeasing] = useState<PartyRef | null>(null);
  const [cessation, setCessation] = useState({ date: '', note: '' });

  const load = async () => {
    try {
      const [p, a] = await Promise.all([
        apiClient.get<PartyRef[]>('/api/privacypa/parties'),
        apiClient.get<Agreement[]>('/api/privacypa/parties/agreements'),
      ]);
      setItems(p); setAgreements(a);
    } catch (e: any) { setError(e.message); }
  };
  useEffect(() => { load(); }, []);

  const current = useMemo(() => items.filter((p) => !isArchived(p)), [items]);
  const appointed = useMemo(() => current.filter((p) => APPOINTED_TYPES.includes(p.party_type)), [current]);
  const archived = useMemo(() => items.filter(isArchived), [items]);
  const incomplete = current.filter((p) => !p.identified || p.appointment?.state === 'UNDEFINED').length;

  const saveAgr = async () => {
    if (!agr.party_id) { setError(t('common.fill_required')); return; }
    try {
      await apiClient.post('/api/privacypa/parties/agreements', {
        ...agr, expiry_date: agr.expiry_date || null,
      });
      setAgrOpen(false); await load();
    } catch (e: any) { setError(e.message); }
  };

  const setCeased = async (party: PartyRef, ceasedOn: string | null, note?: string) => {
    try {
      await apiClient.put(`/api/privacypa/parties/${party.id}/cessation`, { ceased_on: ceasedOn, note: note || null }, { version: party.row_version });
      setCeasing(null); await load();
    } catch (e: any) { setError(e.message); }
  };

  const partyName = (id: string) => items.find((p) => p.id === id)?.legal_name || id;
  const edit = (party: PartyRef) => setDialog({ party, natural: Boolean(party.natural_person) });

  const nameCell = (row: PartyRef) => (
    <TableCell>
      {row.legal_name}
      {row.natural_person && <Chip size="small" variant="outlined" label={t('parties.kind.person')} sx={{ ml: 1 }} />}
    </TableCell>
  );
  const idCell = (row: PartyRef) => (
    <TableCell sx={{ fontFamily: 'monospace' }}>
      {ids(row) || '—'}
      {!row.identified && <Chip size="small" color="error" label={t('parties.identity.incomplete')} sx={{ ml: 1, fontFamily: 'inherit' }} />}
    </TableCell>
  );

  return (
    <PramaLayout pageTitle={t('parties.title')}>
      <Container maxWidth="lg">
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
          <BusinessIcon color="primary" />
          <Typography variant="h4" fontWeight={600}>{t('parties.title')}</Typography>
        </Box>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>{t('parties.subtitle')}</Typography>
        <ErrorBanner message={error} onClose={() => setError(null)} />
        {incomplete > 0 && <Alert severity="warning" sx={{ mb: 2 }}>{t('parties.incomplete_banner', { count: incomplete })}</Alert>}
        <Paper>
          <Tabs value={tab} onChange={(_, v) => setTab(v)} variant="scrollable"
            sx={{ borderBottom: 1, borderColor: 'divider', '& .MuiTab-root': { textTransform: 'none' } }}>
            <Tab label={`${t('parties.tabs.registry')} (${current.length})`} />
            <Tab label={`${t('parties.tabs.appointments')} (${appointed.length})`} />
            <Tab label={t('parties.tabs.agreements')} />
            <Tab label={`${t('parties.tabs.archive')} (${archived.length})`} />
          </Tabs>

          {tab === 0 && (
            <Box sx={{ p: 2, overflowX: 'auto' }}>
              {can('party.create') && (
                <Box sx={{ display: 'flex', gap: 1, mb: 2, flexWrap: 'wrap' }}>
                  <Button variant="contained" startIcon={<AddIcon />} onClick={() => setDialog({ party: null, natural: false })}>{t('parties.add')}</Button>
                  <Button variant="outlined" startIcon={<PersonAddIcon />} onClick={() => setDialog({ party: null, natural: true })}>{t('parties.add_person')}</Button>
                </Box>
              )}
              <Table size="small">
                <TableHead><TableRow>
                  <TableCell>{t('parties.code')}</TableCell>
                  <TableCell>{t('parties.name')}</TableCell>
                  <TableCell>{t('parties.type')}</TableCell>
                  <TableCell>{t('parties.identifiers')}</TableCell>
                  <TableCell>{t('parties.appointment.title')}</TableCell>
                  <TableCell>PEC</TableCell>
                </TableRow></TableHead>
                <TableBody>
                  {current.map((row) => (
                    <TableRow key={row.id} hover sx={{ cursor: 'pointer' }} onClick={() => edit(row)}>
                      <TableCell sx={{ fontFamily: 'monospace' }}>{row.code} <AnnotationsButton entityType="party" entityId={row.id} title={row.code} /></TableCell>
                      {nameCell(row)}
                      <TableCell><Chip size="small" label={t(`parties.types.${row.party_type}`)} /></TableCell>
                      {idCell(row)}
                      <TableCell><AppointmentChip appointment={row.appointment} /></TableCell>
                      <TableCell>{row.pec || '—'}</TableCell>
                    </TableRow>
                  ))}
                  {current.length === 0 && (
                    <TableRow><TableCell colSpan={6}><Typography variant="body2" color="text.secondary">{t('parties.empty')}</Typography></TableCell></TableRow>
                  )}
                </TableBody>
              </Table>
            </Box>
          )}

          {tab === 1 && (
            <Box sx={{ p: 2, overflowX: 'auto' }}>
              <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>{t('parties.appointments_hint')}</Typography>
              <Table size="small">
                <TableHead><TableRow>
                  <TableCell>{t('parties.name')}</TableCell>
                  <TableCell>{t('parties.type')}</TableCell>
                  <TableCell>{t('parties.term.start')}</TableCell>
                  <TableCell>{t('parties.appointment.title')}</TableCell>
                  <TableCell>{t('parties.appointment.basis')}</TableCell>
                  <TableCell>{t('parties.tabs.agreements')}</TableCell>
                  <TableCell />
                </TableRow></TableHead>
                <TableBody>
                  {appointed.map((row) => (
                    <TableRow key={row.id} hover>
                      {nameCell(row)}
                      <TableCell>{t(`parties.types.${row.party_type}`)}</TableCell>
                      <TableCell>{row.appointment?.start || '—'}</TableCell>
                      <TableCell><AppointmentChip appointment={row.appointment} /></TableCell>
                      <TableCell>
                        {row.appointment?.source === 'CONTRACT'
                          ? `${row.appointment.contract_code} — ${row.appointment.contract_title}`
                          : row.appointment?.source === 'DATE' ? t('parties.appointment.by_date') : '—'}
                      </TableCell>
                      <TableCell>
                        {row.party_type === 'PROCESSOR' && (hasValidAgreement(row.id, agreements)
                          ? <Chip size="small" color="success" variant="outlined" label={t('parties.agr.valid')} />
                          : <Chip size="small" color="warning" label={t('parties.agr.missing')} />)}
                      </TableCell>
                      <TableCell align="right" sx={{ whiteSpace: 'nowrap' }}>
                        {can('party.edit') && <Button size="small" onClick={() => edit(row)}>{t('common.edit')}</Button>}
                        {can('party.edit') && (
                          <Button size="small" color="error" onClick={() => {
                            setCessation({ date: new Date().toLocaleDateString('sv-SE'), note: '' });
                            setCeasing(row);
                          }}>{t('parties.cease')}</Button>
                        )}
                      </TableCell>
                    </TableRow>
                  ))}
                  {appointed.length === 0 && (
                    <TableRow><TableCell colSpan={7}><Typography variant="body2" color="text.secondary">{t('parties.appointments_empty')}</Typography></TableCell></TableRow>
                  )}
                </TableBody>
              </Table>
            </Box>
          )}

          {tab === 2 && (
            <Box sx={{ p: 2, overflowX: 'auto' }}>
              {can('party.create') && (
                <Button variant="contained" startIcon={<AddIcon />} sx={{ mb: 2 }} onClick={() => { setAgr(emptyAgr); setAgrOpen(true); }}>{t('parties.add_agreement')}</Button>
              )}
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

          {tab === 3 && (
            <Box sx={{ p: 2, overflowX: 'auto' }}>
              <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>{t('parties.archive_hint')}</Typography>
              <Table size="small">
                <TableHead><TableRow>
                  <TableCell>{t('parties.code')}</TableCell>
                  <TableCell>{t('parties.name')}</TableCell>
                  <TableCell>{t('parties.type')}</TableCell>
                  <TableCell>{t('parties.identifiers')}</TableCell>
                  <TableCell>{t('parties.term.start')}</TableCell>
                  <TableCell>{t('parties.appointment.ended')}</TableCell>
                  <TableCell>{t('parties.cessation.note')}</TableCell>
                  <TableCell />
                </TableRow></TableHead>
                <TableBody>
                  {archived.map((row) => (
                    <TableRow key={row.id} hover>
                      <TableCell sx={{ fontFamily: 'monospace' }}>{row.code}</TableCell>
                      {nameCell(row)}
                      <TableCell>{t(`parties.types.${row.party_type}`)}</TableCell>
                      {idCell(row)}
                      <TableCell>{row.appointment?.start || '—'}</TableCell>
                      <TableCell>
                        {row.appointment?.ceased_on
                          ? t('parties.appointment.ceased_early', { date: row.appointment.ceased_on })
                          : t('parties.appointment.expired', { date: row.appointment?.until })}
                      </TableCell>
                      <TableCell>{row.appointment?.cessation_note || '—'}</TableCell>
                      <TableCell align="right" sx={{ whiteSpace: 'nowrap' }}>
                        {can('party.edit') && (row.appointment?.ceased_on
                          ? <Button size="small" onClick={() => setCeased(row, null)}>{t('parties.reinstate')}</Button>
                          : <Button size="small" onClick={() => edit(row)}>{t('parties.renew')}</Button>)}
                      </TableCell>
                    </TableRow>
                  ))}
                  {archived.length === 0 && (
                    <TableRow><TableCell colSpan={8}><Typography variant="body2" color="text.secondary">{t('parties.archive_empty')}</Typography></TableCell></TableRow>
                  )}
                </TableBody>
              </Table>
            </Box>
          )}
        </Paper>
      </Container>

      <PartyDialog
        open={dialog !== null}
        party={dialog?.party}
        naturalPerson={dialog?.natural}
        defaultType={dialog?.natural ? 'OTHER' : 'PROCESSOR'}
        onClose={() => setDialog(null)}
        onSaved={async () => { setDialog(null); await load(); }}
      />

      <Dialog open={ceasing !== null} onClose={() => setCeasing(null)} maxWidth="xs" fullWidth>
        <DialogTitle>{t('parties.cessation.title', { name: ceasing?.legal_name })}</DialogTitle>
        <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: '8px !important' }}>
          <Typography variant="body2" color="text.secondary">{t('parties.cessation.hint')}</Typography>
          <TextField type="date" label={t('parties.cessation.date')} InputLabelProps={{ shrink: true }} required
            value={cessation.date} onChange={(e) => setCessation({ ...cessation, date: e.target.value })} />
          <TextField label={t('parties.cessation.note')} multiline minRows={2} value={cessation.note}
            onChange={(e) => setCessation({ ...cessation, note: e.target.value })} />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setCeasing(null)}>{t('common.cancel')}</Button>
          <Button variant="contained" color="error" disabled={!cessation.date}
            onClick={() => ceasing && setCeased(ceasing, cessation.date, cessation.note)}>{t('parties.cease')}</Button>
        </DialogActions>
      </Dialog>

      <Dialog open={agrOpen} onClose={() => setAgrOpen(false)} maxWidth="sm" fullWidth>
        <DialogTitle>{t('parties.add_agreement')}</DialogTitle>
        <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 2 }}>
          <SinglePick
            label={t('parties.name')} required
            items={current.filter((p) => APPOINTED_TYPES.includes(p.party_type)).map((p) => ({ id: p.id, label: p.legal_name }))}
            value={agr.party_id} onChange={(id) => setAgr((a) => ({ ...a, party_id: id }))}
          />
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
