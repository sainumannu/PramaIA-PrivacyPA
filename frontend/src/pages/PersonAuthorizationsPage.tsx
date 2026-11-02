import React, { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  Alert, Box, Button, Chip, Collapse, Container, GlobalStyles, Grid, IconButton, Paper, Stack, Table, TableBody, TableCell, TableHead,
  TableRow, TextField, Tooltip, Typography,
} from '@mui/material';
import ManageAccountsIcon from '@mui/icons-material/ManageAccounts';
import KeyboardArrowDownIcon from '@mui/icons-material/KeyboardArrowDown';
import KeyboardArrowUpIcon from '@mui/icons-material/KeyboardArrowUp';
import PrintIcon from '@mui/icons-material/Print';
import FileDownloadIcon from '@mui/icons-material/FileDownload';
import { PramaLayout } from '../components/PramaLayout';
import { ErrorBanner } from '../components/ErrorBanner';
import { PersonField, PersonName, usePortalPeople } from '../components/PortalPeople';
import { apiClient } from '../services/apiClient';
import { useTranslation } from '../i18n/useTranslation';

// A cosa era abilitata una persona alla fine di un giorno e in virtù di quali autorizzazioni (valori di allora).
const AU = '/api/privacypa/authorizations';
const today = () => new Date().toISOString().slice(0, 10);
const day = (iso: string | null | undefined) => (iso ? iso.slice(0, 10) : '—');
const STATUS_COLOR: Record<string, 'success' | 'info' | 'warning' | 'default'> = {
  ACTIVE: 'success', NOT_STARTED: 'info', EXPIRED: 'warning', REVOKED: 'default',
};
// Campi confrontati tra una versione e la precedente nello storico.
const TRACKED = [
  'processing_activity_id', 'asset_id', 'authorization_profile_id', 'profile_version', 'operations', 'scope', 'valid_from',
  'valid_to', 'authorized_by_name', 'authorization_reference', 'authorization_date', 'authorization_notes', 'revoked_reason',
  'revocation_reference', 'last_reviewed_at',
];

interface Training { course_id: string; title: string | null; state: 'DONE' | 'EXPIRED' | 'MISSING'; completed_at: string | null; next_due_at: string | null }
interface Entry {
  authorization_id: string;
  values: Record<string, any>;
  status_at: string;
  registered_after: boolean;
  changed_after: boolean;
  tracked_since: string | null;
  profile_name: string | null;
  profile_current_version: number | null;
  asset_name: string | null;
  processing_name: string | null;
  training: Training[];
}
interface Snapshot { id: string; action: string; actor_user_id: string | null; recorded_at: string; data: Record<string, any> }

const History: React.FC<{ id: string }> = ({ id }) => {
  const { t } = useTranslation('privacypa');
  const [rows, setRows] = useState<Snapshot[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    apiClient.get<Snapshot[]>(`${AU}/${id}/history`).then(setRows).catch((e) => setError(e.message));
  }, [id]);
  if (error) return <Alert severity="error">{error}</Alert>;
  if (!rows) return null;
  if (!rows.length) return <Typography variant="body2" color="text.secondary">{t('people.person_view.no_history')}</Typography>;
  return (
    <Table size="small">
      <TableBody>
        {rows.map((s, i) => {
          const before = i > 0 ? rows[i - 1].data : null;
          const changed = before ? TRACKED.filter((k) => JSON.stringify(before[k] ?? null) !== JSON.stringify(s.data[k] ?? null)) : [];
          return (
            <TableRow key={s.id}>
              <TableCell sx={{ whiteSpace: 'nowrap' }}>{new Date(s.recorded_at + 'Z').toLocaleString()}</TableCell>
              <TableCell>{t(`people.person_view.action.${s.action}`)}</TableCell>
              <TableCell>{s.actor_user_id ? <PersonName id={s.actor_user_id} /> : '—'}</TableCell>
              <TableCell>{changed.map((k) => t(`wf.fields.${k}`)).join(', ')}</TableCell>
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
  );
};

const Row: React.FC<{ entry: Entry; asOf: string }> = ({ entry, asOf }) => {
  const { t } = useTranslation('privacypa');
  const [open, setOpen] = useState(false);
  const v = entry.values;
  const ops = (v.operations || []).map((o: string) => t(`wf.enum.accessOperation.${o}`)).join(', ');
  const scope = v.scope ? `${t(`wf.enum.accessScope.${v.scope.type}`)}${v.scope.value ? ` — ${v.scope.value}` : ''}` : '';
  const notes = [
    entry.registered_after && t('people.person_view.registered_after', { date: day(v.granted_at) }),
    entry.changed_after && t('people.person_view.changed_after'),
    // registrata prima che esistesse lo storico: le modifiche di allora non sono ricostruibili
    !entry.tracked_since ? t('people.person_view.untracked')
      : asOf < entry.tracked_since.slice(0, 10) && !entry.registered_after
        && t('people.person_view.tracked_since', { date: day(entry.tracked_since) }),
    entry.profile_current_version && v.profile_version && entry.profile_current_version !== v.profile_version
      && t('people.person_view.profile_changed', { version: entry.profile_current_version }),
  ].filter(Boolean) as string[];

  return (
    <>
      <TableRow sx={{ '& > td': { borderBottom: 'unset' }, verticalAlign: 'top' }}>
        <TableCell padding="checkbox">
          <Tooltip title={t('people.person_view.history')}>
            <IconButton size="small" onClick={() => setOpen(!open)} className="no-print">
              {open ? <KeyboardArrowUpIcon /> : <KeyboardArrowDownIcon />}
            </IconButton>
          </Tooltip>
        </TableCell>
        <TableCell>
          <Chip size="small" color={STATUS_COLOR[entry.status_at]} label={t(`people.person_view.status.${entry.status_at}`)} />
        </TableCell>
        <TableCell>
          <Typography variant="body2" sx={{ fontWeight: 600 }}>{entry.asset_name || entry.processing_name || '—'}</Typography>
          {entry.asset_name && entry.processing_name && <Typography variant="caption" color="text.secondary">{entry.processing_name}</Typography>}
        </TableCell>
        <TableCell>
          <Typography variant="body2">{entry.profile_name || '—'}</Typography>
          {v.profile_version && <Typography variant="caption" color="text.secondary">{t('people.profile_version', { version: v.profile_version })}</Typography>}
        </TableCell>
        <TableCell>
          <Typography variant="body2">{ops || '—'}</Typography>
          {scope && <Typography variant="caption" color="text.secondary">{scope}</Typography>}
        </TableCell>
        <TableCell sx={{ whiteSpace: 'nowrap' }}>{`${day(v.valid_from)} → ${day(v.valid_to)}`}</TableCell>
        <TableCell>
          <Typography variant="body2">{v.authorization_reference || <Box component="span" sx={{ color: 'warning.main' }}>{t('people.person_view.no_reference')}</Box>}</Typography>
          <Typography variant="caption" color="text.secondary" component="div">
            {[v.authorized_by_name, v.authorization_date && day(v.authorization_date)].filter(Boolean).join(' · ')}
          </Typography>
          {v.source_ref && <Typography variant="caption" color="text.secondary" component="div">{`${v.source_module || ''} ${v.source_ref}`}</Typography>}
          {v.forced && <Chip size="small" color="warning" label={t('people.forced')} sx={{ mt: 0.5 }} />}
        </TableCell>
        <TableCell>
          <Stack spacing={0.5} alignItems="flex-start">
            {entry.training.length === 0 ? '—' : entry.training.map((tr) => (
              <Tooltip key={tr.course_id} title={tr.completed_at ? t('people.person_view.training_done_on', { date: day(tr.completed_at) }) : ''}>
                <Chip size="small" variant="outlined" color={tr.state === 'DONE' ? 'success' : 'error'}
                  label={`${tr.title || tr.course_id}: ${t(`people.person_view.training.${tr.state}`)}`} />
              </Tooltip>
            ))}
          </Stack>
        </TableCell>
      </TableRow>
      <TableRow>
        <TableCell colSpan={8} sx={{ py: 0 }}>
          {(notes.length > 0 || v.revoked_at) && (
            <Box sx={{ pb: 1 }}>
              {v.revoked_at && (
                <Typography variant="caption" component="div">
                  {t('people.person_view.revoked_on', { date: day(v.revoked_at), reason: v.revoked_reason || '—', reference: v.revocation_reference || '—' })}
                </Typography>
              )}
              {notes.map((n) => <Typography key={n} variant="caption" color="text.secondary" component="div">{n}</Typography>)}
            </Box>
          )}
          <Collapse in={open} timeout="auto" unmountOnExit>
            <Box sx={{ pb: 2 }}>
              <Typography variant="subtitle2" sx={{ mb: 1 }}>{t('people.person_view.history')}</Typography>
              <History id={entry.authorization_id} />
            </Box>
          </Collapse>
        </TableCell>
      </TableRow>
    </>
  );
};

const csvCell = (value: unknown) => `"${String(value ?? '').replace(/"/g, '""')}"`;

export const PersonAuthorizationsPage: React.FC = () => {
  const { t } = useTranslation('privacypa');
  const { nameOf } = usePortalPeople();
  const [params, setParams] = useSearchParams();
  const userId = params.get('user') || '';
  const asOf = params.get('date') || today();
  const [entries, setEntries] = useState<Entry[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const setQuery = (user: string, date: string) => {
    const next: Record<string, string> = {};
    if (user) next.user = user;
    if (date && date !== today()) next.date = date;
    setParams(next, { replace: true });
  };

  useEffect(() => {
    if (!userId) {
      setEntries(null);
      return;
    }
    apiClient.get<{ entries: Entry[] }>(`${AU}/person/${encodeURIComponent(userId)}?as_of=${asOf}`)
      .then((r) => setEntries(r.entries))
      .catch((e) => setError(e.message));
  }, [userId, asOf]);

  const valid = (entries || []).filter((e) => e.status_at === 'ACTIVE');
  const others = (entries || []).filter((e) => e.status_at !== 'ACTIVE');

  const exportCsv = () => {
    const header = ['status', 'system', 'processing', 'profile', 'operations', 'valid_from', 'valid_to', 'authorized_by',
      'reference', 'authorization_date', 'source', 'training'].map((k) => t(`people.person_view.csv.${k}`));
    const lines = (entries || []).map((e) => [
      t(`people.person_view.status.${e.status_at}`), e.asset_name, e.processing_name, e.profile_name,
      (e.values.operations || []).join(' '), day(e.values.valid_from), day(e.values.valid_to), e.values.authorized_by_name,
      e.values.authorization_reference, day(e.values.authorization_date),
      e.values.source_ref ? `${e.values.source_module || ''} ${e.values.source_ref}` : '',
      e.training.map((tr) => `${tr.title}: ${t(`people.person_view.training.${tr.state}`)}`).join('; '),
    ].map(csvCell).join(';'));
    const blob = new Blob(['﻿' + [header.map(csvCell).join(';'), ...lines].join('\r\n')], { type: 'text/csv;charset=utf-8' });
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = `abilitazioni_${nameOf(userId).replace(/[^\w-]+/g, '_')}_${asOf}.csv`;
    link.click();
    URL.revokeObjectURL(link.href);
  };

  const table = (rows: Entry[]) => (
    <Table size="small">
      <TableHead>
        <TableRow>
          <TableCell padding="checkbox" />
          {['status', 'system', 'profile', 'operations', 'period', 'grounds', 'training'].map((k) => (
            <TableCell key={k}>{t(`people.person_view.columns.${k}`)}</TableCell>
          ))}
        </TableRow>
      </TableHead>
      <TableBody>
        {rows.map((e) => <Row key={e.authorization_id} entry={e} asOf={asOf} />)}
      </TableBody>
    </Table>
  );

  return (
    <PramaLayout pageTitle={t('people.person_view.title')}>
      {/* in stampa restano solo l'intestazione e le tabelle: niente menu, barra in alto e filtri */}
      <GlobalStyles styles={{ '@media print': {
        '.MuiDrawer-root, .MuiAppBar-root, .no-print': { display: 'none !important' },
        main: { margin: '0 !important', padding: '0 !important', width: '100% !important' },
      } }} />
      <Container maxWidth="xl">
        <ErrorBanner message={error} onClose={() => setError(null)} />
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
          <ManageAccountsIcon sx={{ fontSize: 28, color: 'primary.main' }} />
          <Typography variant="h4" sx={{ fontWeight: 600 }}>{t('people.person_view.title')}</Typography>
        </Box>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>{t('people.person_view.subtitle')}</Typography>

        <Paper sx={{ p: 2, mb: 3 }} className="no-print">
          <Grid container spacing={2} alignItems="flex-start">
            <Grid item xs={12} md={6}>
              <PersonField label={t('wf.fields.user_id')} value={userId} onChange={(id) => setQuery(id, asOf)} />
            </Grid>
            <Grid item xs={12} sm={6} md={3}>
              <TextField type="date" label={t('people.person_view.as_of')} value={asOf} fullWidth InputLabelProps={{ shrink: true }}
                helperText={t('people.person_view.as_of_hint')} onChange={(e) => setQuery(userId, e.target.value || today())} />
            </Grid>
            <Grid item xs={12} sm={6} md={3}>
              <Stack direction="row" spacing={1}>
                <Button variant="outlined" startIcon={<PrintIcon />} disabled={!entries?.length} onClick={() => window.print()}>
                  {t('people.person_view.print')}
                </Button>
                <Button variant="outlined" startIcon={<FileDownloadIcon />} disabled={!entries?.length} onClick={exportCsv}>CSV</Button>
              </Stack>
            </Grid>
          </Grid>
        </Paper>

        {!userId ? (
          <Alert severity="info">{t('people.person_view.pick_person')}</Alert>
        ) : entries && (
          <>
            <Typography variant="h6" sx={{ mb: 1 }}>
              {t('people.person_view.heading', { name: nameOf(userId), date: new Date(asOf).toLocaleDateString() })}
            </Typography>
            <Paper sx={{ mb: 3, overflowX: 'auto' }}>
              <Typography variant="subtitle1" sx={{ p: 2, pb: 0 }}>{t('people.person_view.valid', { count: valid.length })}</Typography>
              {valid.length ? table(valid) : <Typography variant="body2" color="text.secondary" sx={{ p: 2 }}>{t('people.person_view.none_valid')}</Typography>}
            </Paper>
            {others.length > 0 && (
              <Paper sx={{ mb: 3, overflowX: 'auto' }}>
                <Typography variant="subtitle1" sx={{ p: 2, pb: 0 }}>{t('people.person_view.others', { count: others.length })}</Typography>
                {table(others)}
              </Paper>
            )}
          </>
        )}
      </Container>
    </PramaLayout>
  );
};

export default PersonAuthorizationsPage;
