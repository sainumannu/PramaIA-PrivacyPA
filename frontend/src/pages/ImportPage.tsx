import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Alert, Box, Button, Checkbox, Chip, Container, FormControl, FormControlLabel, InputLabel, MenuItem,
  Paper, Select, Step, StepLabel, Stepper, Table, TableBody, TableCell, TableHead, TableRow, Typography,
} from '@mui/material';
import UploadFileIcon from '@mui/icons-material/UploadFile';
import { PramaLayout } from '../components/PramaLayout';
import { apiClient } from '../services/apiClient';
import { useTranslation } from '../i18n/useTranslation';

const BASE = '/api/privacypa/imports/processing-activities';

const FIELDS = [
  'title', 'unit', 'description', 'purposes', 'legal_bases', 'data_subjects', 'data_categories',
  'recipients', 'processors', 'assets', 'source_of_data', 'collection_method', 'start_date',
  'last_review_date', 'next_review_date', 'mandatory_provision', 'automated_decision', 'profiling',
  'owner_note', 'retention_note', 'notes',
];

interface SheetInfo { name: string; columns: string[]; row_count: number; suggested_mapping: Record<string, string> }
interface Anomaly { row: number; field: string; code: string; severity: string; message: string; value: string | null }
interface Counts {
  rows_total: number; ok: number; invalid: number; duplicates: number; warnings: number;
  to_import: number; can_commit: boolean; blocked_by_invalid_rows: boolean;
}
interface Report {
  counts: Counts; anomalies: Anomaly[]; anomalies_truncated: boolean;
  preview: { row: number; outcome: string; title: string }[];
  commit?: { created: number; skipped_duplicates: number; skipped_invalid: number; first_code: string; last_code: string };
}

const MAX_ANOMALIES_SHOWN = 100;

export const ImportPage: React.FC = () => {
  const { t } = useTranslation('privacypa');
  const navigate = useNavigate();
  const [step, setStep] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [sheets, setSheets] = useState<SheetInfo[]>([]);
  const [sheetName, setSheetName] = useState('');
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [onDuplicate, setOnDuplicate] = useState<'skip' | 'import'>('skip');
  const [skipInvalid, setSkipInvalid] = useState(false);
  const [report, setReport] = useState<Report | null>(null);

  const sheet = sheets.find((s) => s.name === sheetName);
  const used = Object.values(mapping).filter(Boolean);
  const hasDuplicateColumn = new Set(used).size !== used.length;
  const canVerify = !!mapping.title && !hasDuplicateColumn;

  const run = async (fn: () => Promise<void>) => {
    setBusy(true); setError(null);
    try { await fn(); } catch (e: any) { setError(e.message); } finally { setBusy(false); }
  };

  const onFile = (file: File | undefined) => {
    if (!file) return;
    run(async () => {
      const form = new FormData();
      form.append('file', file);
      const res = await apiClient.upload<{ job: { id: string }; sheets: SheetInfo[] }>(BASE, form);
      setJobId(res.job.id);
      setSheets(res.sheets);
      const first = res.sheets.find((s) => s.row_count > 0) || res.sheets[0];
      setSheetName(first.name);
      setMapping(first.suggested_mapping);
      setStep(1);
    });
  };

  const changeSheet = (name: string) => {
    setSheetName(name);
    setMapping(sheets.find((s) => s.name === name)?.suggested_mapping || {});
  };

  const verify = () => run(async () => {
    const cleaned = Object.fromEntries(Object.entries(mapping).filter(([, col]) => col));
    setReport(await apiClient.post<Report>(`${BASE}/${jobId}/dry-run`, {
      sheet: sheetName, mapping: cleaned, on_duplicate: onDuplicate, skip_invalid_rows: skipInvalid,
    }));
    setStep(2);
  });

  const commit = () => run(async () => {
    setReport(await apiClient.post<Report>(`${BASE}/${jobId}/commit`, { confirm: true }));
    setStep(3);
  });

  const discard = () => run(async () => {
    if (jobId && step < 3) await apiClient.delete(`${BASE}/${jobId}`);
    setStep(0); setJobId(null); setSheets([]); setReport(null); setMapping({});
  });

  return (
    <PramaLayout pageTitle={t('import.title')}>
      <Container maxWidth="lg">
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
          <UploadFileIcon color="primary" />
          <Typography variant="h5" component="h1">{t('import.title')}</Typography>
        </Box>
        <Typography color="text.secondary" sx={{ mb: 2 }}>{t('import.subtitle')}</Typography>
        <Stepper activeStep={step} sx={{ mb: 3 }}>
          {['upload', 'mapping', 'verify', 'done'].map((k) => (
            <Step key={k}><StepLabel>{t(`import.steps.${k}`)}</StepLabel></Step>
          ))}
        </Stepper>
        {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>{error}</Alert>}

        {step === 0 && (
          <Paper sx={{ p: 3 }}>
            <Typography sx={{ mb: 2 }}>{t('import.upload_hint')}</Typography>
            <Button variant="contained" component="label" disabled={busy} startIcon={<UploadFileIcon />}>
              {t('import.choose_file')}
              <input type="file" hidden accept=".xlsx,.csv" onChange={(e) => onFile(e.target.files?.[0])} />
            </Button>
          </Paper>
        )}

        {step === 1 && sheet && (
          <Paper sx={{ p: 3 }}>
            {sheets.length > 1 && (
              <FormControl size="small" sx={{ minWidth: 240, mb: 2 }}>
                <InputLabel id="sheet-label">{t('import.sheet')}</InputLabel>
                <Select labelId="sheet-label" label={t('import.sheet')} value={sheetName}
                  onChange={(e) => changeSheet(e.target.value)}>
                  {sheets.map((s) => <MenuItem key={s.name} value={s.name}>{s.name} ({s.row_count})</MenuItem>)}
                </Select>
              </FormControl>
            )}
            <Typography variant="subtitle1" sx={{ mb: 1 }}>{t('import.mapping_hint')}</Typography>
            <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', md: '1fr 1fr' }, gap: 2 }}>
              {FIELDS.map((f) => (
                <FormControl key={f} size="small" fullWidth required={f === 'title'}
                  error={f === 'title' && !mapping.title}>
                  <InputLabel id={`map-${f}`}>{t(`import.fields.${f}`)}</InputLabel>
                  <Select labelId={`map-${f}`} label={t(`import.fields.${f}`)} value={mapping[f] || ''}
                    onChange={(e) => setMapping({ ...mapping, [f]: e.target.value })}>
                    <MenuItem value=""><em>{t('import.not_mapped')}</em></MenuItem>
                    {sheet.columns.map((c) => <MenuItem key={c} value={c}>{c}</MenuItem>)}
                  </Select>
                </FormControl>
              ))}
            </Box>
            {hasDuplicateColumn && <Alert severity="warning" sx={{ mt: 2 }}>{t('import.duplicate_column')}</Alert>}
            <Box sx={{ mt: 2, display: 'flex', flexWrap: 'wrap', gap: 2, alignItems: 'center' }}>
              <FormControl size="small" sx={{ minWidth: 260 }}>
                <InputLabel id="dup-label">{t('import.on_duplicate')}</InputLabel>
                <Select labelId="dup-label" label={t('import.on_duplicate')} value={onDuplicate}
                  onChange={(e) => setOnDuplicate(e.target.value as 'skip' | 'import')}>
                  <MenuItem value="skip">{t('import.dup_skip')}</MenuItem>
                  <MenuItem value="import">{t('import.dup_import')}</MenuItem>
                </Select>
              </FormControl>
              <FormControlLabel
                control={<Checkbox checked={skipInvalid} onChange={(e) => setSkipInvalid(e.target.checked)} />}
                label={t('import.skip_invalid')}
              />
            </Box>
            <Box sx={{ mt: 3, display: 'flex', gap: 1 }}>
              <Button onClick={discard} disabled={busy}>{t('import.discard')}</Button>
              <Button variant="contained" onClick={verify} disabled={busy || !canVerify}>{t('import.verify')}</Button>
            </Box>
          </Paper>
        )}

        {step === 2 && report && (
          <Paper sx={{ p: 3 }}>
            <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 1, mb: 2 }}>
              <Chip label={`${t('import.counts.rows_total')}: ${report.counts.rows_total}`} />
              <Chip color="success" label={`${t('import.counts.to_import')}: ${report.counts.to_import}`} />
              <Chip color={report.counts.invalid ? 'error' : 'default'} label={`${t('import.counts.invalid')}: ${report.counts.invalid}`} />
              <Chip label={`${t('import.counts.duplicates')}: ${report.counts.duplicates}`} />
              <Chip color={report.counts.warnings ? 'warning' : 'default'} label={`${t('import.counts.warnings')}: ${report.counts.warnings}`} />
            </Box>
            {report.counts.blocked_by_invalid_rows && <Alert severity="error" sx={{ mb: 2 }}>{t('import.blocked')}</Alert>}
            <Alert severity="info" sx={{ mb: 2 }}>{t('import.drafts_note')}</Alert>

            {report.anomalies.length > 0 && (
              <>
                <Typography variant="subtitle1" sx={{ mb: 1 }}>{t('import.anomalies')}</Typography>
                <Table size="small" sx={{ mb: 2 }}>
                  <TableHead><TableRow>
                    <TableCell>{t('import.row')}</TableCell>
                    <TableCell>{t('import.field')}</TableCell>
                    <TableCell>{t('import.severity')}</TableCell>
                    <TableCell>{t('import.message')}</TableCell>
                    <TableCell>{t('import.value')}</TableCell>
                  </TableRow></TableHead>
                  <TableBody>
                    {report.anomalies.slice(0, MAX_ANOMALIES_SHOWN).map((a, i) => (
                      <TableRow key={i}>
                        <TableCell>{a.row}</TableCell>
                        <TableCell>{t(`import.fields.${a.field}`)}</TableCell>
                        <TableCell>
                          <Chip size="small" color={a.severity === 'error' ? 'error' : 'warning'}
                            label={t(`import.sev.${a.severity}`)} />
                        </TableCell>
                        <TableCell>{a.message}</TableCell>
                        <TableCell>{a.value || '—'}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
                {(report.anomalies.length > MAX_ANOMALIES_SHOWN || report.anomalies_truncated) && (
                  <Alert severity="info" sx={{ mb: 2 }}>{t('import.anomalies_truncated', { count: MAX_ANOMALIES_SHOWN })}</Alert>
                )}
              </>
            )}

            <Typography variant="subtitle1" sx={{ mb: 1 }}>{t('import.preview')}</Typography>
            <Table size="small">
              <TableHead><TableRow>
                <TableCell>{t('import.row')}</TableCell>
                <TableCell>{t('import.fields.title')}</TableCell>
                <TableCell>{t('import.outcome')}</TableCell>
              </TableRow></TableHead>
              <TableBody>
                {report.preview.map((p) => (
                  <TableRow key={p.row}>
                    <TableCell>{p.row}</TableCell>
                    <TableCell>{p.title || '—'}</TableCell>
                    <TableCell>{t(`import.out.${p.outcome}`)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>

            <Box sx={{ mt: 3, display: 'flex', gap: 1 }}>
              <Button onClick={() => setStep(1)} disabled={busy}>{t('import.back')}</Button>
              <Button onClick={discard} disabled={busy}>{t('import.discard')}</Button>
              <Button variant="contained" onClick={commit} disabled={busy || !report.counts.can_commit}>
                {t('import.commit', { count: report.counts.to_import })}
              </Button>
            </Box>
          </Paper>
        )}

        {step === 3 && report?.commit && (
          <Paper sx={{ p: 3 }}>
            <Alert severity="success" sx={{ mb: 2 }}>
              {t('import.done', { count: report.commit.created, first: report.commit.first_code, last: report.commit.last_code })}
            </Alert>
            <Typography sx={{ mb: 2 }}>
              {t('import.skipped', { dup: report.commit.skipped_duplicates, invalid: report.commit.skipped_invalid })}
            </Typography>
            <Box sx={{ display: 'flex', gap: 1 }}>
              <Button variant="contained" onClick={() => navigate('/registro')}>{t('import.open_register')}</Button>
              <Button onClick={discard}>{t('import.new_import')}</Button>
            </Box>
          </Paper>
        )}
      </Container>
    </PramaLayout>
  );
};

export default ImportPage;
