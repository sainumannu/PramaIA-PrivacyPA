import React, { useEffect, useMemo, useState } from 'react';
import {
  Alert, Box, Button, Checkbox, Chip, Container, FormControl, IconButton, InputLabel, ListItemText, MenuItem, Paper, Select,
  Table, TableBody, TableCell, TableContainer, TableHead, TablePagination, TableRow, TextField, ToggleButton, ToggleButtonGroup, Typography,
} from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import DeleteIcon from '@mui/icons-material/DeleteOutline';
import ManageSearchIcon from '@mui/icons-material/ManageSearch';
import DownloadIcon from '@mui/icons-material/Download';
import { PramaLayout } from '../components/PramaLayout';
import { apiClient } from '../services/apiClient';
import { useTranslation } from '../i18n/useTranslation';

const API = '/api/privacypa/queries';
const NO_VALUE = ['is_null', 'is_not_null'];
const LIST = ['in', 'not_in'];

interface FieldDef { key: string; type: string; operators: string[]; values: string[] | null; multi: boolean; selectable: boolean; sortable: boolean }
interface EntityDef { key: string; module: string; columns: string[]; fields: FieldDef[] }
interface Schema { operators: string[]; can_export: boolean; entities: EntityDef[] }
interface Condition { field: string; operator: string; value: any }
interface ResultRow { id: string; code: string | null; title: string | null; tags: string[]; values: Record<string, any> }
interface Result { total: number; offset: number; limit: number; columns: { key: string; type: string }[]; rows: ResultRow[] }

const emptyValue = (field: FieldDef, operator: string): any => (LIST.includes(operator) ? [] : field.type === 'bool' || field.type === 'flag' ? 'true' : '');

// Il valore come lo vuole il server: elenchi per «in», booleani e numeri veri, nulla per «è vuoto».
const toPayload = (field: FieldDef, c: Condition) => {
  if (NO_VALUE.includes(c.operator)) return { field: c.field, operator: c.operator };
  const one = (v: any) => (field.type === 'bool' || field.type === 'flag' ? v === true || v === 'true' : field.type === 'number' ? Number(v) : v);
  if (LIST.includes(c.operator)) {
    const items = Array.isArray(c.value) ? c.value : String(c.value || '').split(',').map((v) => v.trim()).filter(Boolean);
    return { field: c.field, operator: c.operator, value: items.map(one) };
  }
  return { field: c.field, operator: c.operator, value: one(c.value) };
};

const ValueInput: React.FC<{ field: FieldDef; cond: Condition; onChange: (v: any) => void }> = ({ field, cond, onChange }) => {
  const { t } = useTranslation('privacypa');
  if (NO_VALUE.includes(cond.operator)) return null;
  const label = t('query.value');
  const list = LIST.includes(cond.operator);
  if (field.type === 'enum') {
    return (
      <FormControl size="small" sx={{ minWidth: 220, flex: 1 }}>
        <InputLabel>{label}</InputLabel>
        <Select
          label={label} multiple={list} value={list ? (Array.isArray(cond.value) ? cond.value : []) : cond.value}
          renderValue={(v) => (Array.isArray(v) ? v.join(', ') : String(v))}
          onChange={(e) => onChange(e.target.value)}
        >
          {(field.values || []).map((v) => (
            <MenuItem key={v} value={v}>{list && <Checkbox size="small" checked={(cond.value || []).includes(v)} />}<ListItemText primary={t(`query.values.${v}`, { defaultValue: v })} /></MenuItem>
          ))}
        </Select>
      </FormControl>
    );
  }
  if (field.type === 'bool' || field.type === 'flag') {
    return (
      <FormControl size="small" sx={{ minWidth: 140 }}>
        <InputLabel>{label}</InputLabel>
        <Select label={label} value={String(cond.value)} onChange={(e) => onChange(e.target.value)}>
          <MenuItem value="true">{t('query.yes')}</MenuItem>
          <MenuItem value="false">{t('query.no')}</MenuItem>
        </Select>
      </FormControl>
    );
  }
  if (field.type === 'date' || field.type === 'datetime') {
    return <TextField size="small" type="date" label={label} value={cond.value} InputLabelProps={{ shrink: true }} onChange={(e) => onChange(e.target.value)} />;
  }
  return (
    <TextField
      size="small" sx={{ minWidth: 220, flex: 1 }} label={label} value={Array.isArray(cond.value) ? cond.value.join(', ') : cond.value}
      type={field.type === 'number' && !list ? 'number' : 'text'} helperText={list ? t('query.list_hint') : undefined}
      onChange={(e) => onChange(e.target.value)}
    />
  );
};

const Cell: React.FC<{ type: string; value: any }> = ({ type, value }) => {
  const { t } = useTranslation('privacypa');
  if (value === null || value === undefined || value === '') return <>—</>;
  if (type === 'bool') return <>{value ? t('query.yes') : t('query.no')}</>;
  if (type === 'enum') return <>{t(`query.values.${value}`, { defaultValue: String(value) })}</>;
  if (type === 'datetime') return <>{String(value).replace('T', ' ').slice(0, 16)}</>;
  return <>{String(value)}</>;
};

// Query builder (§40): condizioni «campo operatore valore» su un tipo di elemento; i risultati sono quelli che l'utente può vedere.
export const QueryBuilderPage: React.FC = () => {
  const { t } = useTranslation('privacypa');
  const [schema, setSchema] = useState<Schema | null>(null);
  const [entityKey, setEntityKey] = useState('');
  const [match, setMatch] = useState<'all' | 'any'>('all');
  const [conds, setConds] = useState<Condition[]>([]);
  const [columns, setColumns] = useState<string[]>([]);
  const [sort, setSort] = useState('code');
  const [direction, setDirection] = useState<'asc' | 'desc'>('asc');
  const [result, setResult] = useState<Result | null>(null);
  const [rowsPerPage, setRowsPerPage] = useState(25);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);

  useEffect(() => {
    apiClient.get<Schema>(`${API}/schema`).then((s) => {
      setSchema(s);
      const first = s.entities[0];
      if (!first) return;
      setEntityKey(first.key);
      setConds([]);
      setColumns(first.columns);
      setSort('code');
      setDirection('asc');
      setResult(null);
      setInfo(null);
    }).catch((e) => setError(e.message));
  }, []);

  const entity = useMemo(() => schema?.entities.find((e) => e.key === entityKey) || null, [schema, entityKey]);
  const fields = useMemo(() => Object.fromEntries((entity?.fields || []).map((f) => [f.key, f])), [entity]);

  const pickEntity = (e: EntityDef, key: string) => {
    setEntityKey(key); setConds([]); setColumns(e.columns); setSort('code'); setDirection('asc'); setResult(null); setInfo(null);
  };
  const label = (key: string) => t(`query.fields.${key}`, { defaultValue: key });

  const addCondition = () => {
    if (!entity) return;
    const f = entity.fields[0];
    setConds([...conds, { field: f.key, operator: f.operators[0], value: emptyValue(f, f.operators[0]) }]);
  };
  const changeCondition = (i: number, next: Partial<Condition>) => setConds(conds.map((c, j) => {
    if (j !== i) return c;
    const merged = { ...c, ...next };
    const f = fields[merged.field];
    if (next.field) {                                        // altro campo: primo operatore ammesso e valore vuoto
      merged.operator = f.operators.includes(c.operator) ? c.operator : f.operators[0];
      merged.value = emptyValue(f, merged.operator);
    } else if (next.operator) {
      merged.value = LIST.includes(next.operator) !== LIST.includes(c.operator) ? emptyValue(f, next.operator) : c.value;
    }
    return merged;
  }));

  const body = () => ({
    entity: entityKey, match, conditions: conds.map((c) => toPayload(fields[c.field], c)), columns, sort, direction,
  });

  const run = async (offset = 0, limit = rowsPerPage) => {
    setError(null); setInfo(null);
    try {
      setResult(await apiClient.post<Result>(`${API}/run`, { ...body(), offset, limit }));
    } catch (e: any) { setError(e.message); setResult(null); }
  };

  const exportAs = async (format: 'csv' | 'xlsx') => {
    setError(null); setInfo(null);
    try {
      const { excluded } = await apiClient.downloadPost(`${API}/export`, { ...body(), format }, `interrogazione-${entityKey}.${format}`);
      setInfo(excluded ? t('query.exported_excluded', { count: excluded }) : t('query.exported'));
    } catch (e: any) { setError(e.message); }
  };

  const sortable = (entity?.fields || []).filter((f) => f.sortable);
  const selectable = (entity?.fields || []).filter((f) => f.selectable);

  return (
    <PramaLayout pageTitle={t('query.title')}>
      <Container maxWidth="lg">
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
          <ManageSearchIcon color="primary" />
          <Typography variant="h4" fontWeight={600}>{t('query.title')}</Typography>
        </Box>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>{t('query.subtitle')}</Typography>
        {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>{error}</Alert>}
        {info && <Alert severity="success" sx={{ mb: 2 }} onClose={() => setInfo(null)}>{info}</Alert>}

        {schema && entity && (
          <Paper sx={{ p: 2, mb: 2 }}>
            <Box sx={{ display: 'flex', gap: 2, flexWrap: 'wrap', alignItems: 'center', mb: 2 }}>
              <FormControl size="small" sx={{ minWidth: 240 }}>
                <InputLabel>{t('query.entity')}</InputLabel>
                <Select label={t('query.entity')} value={entityKey} onChange={(e) => pickEntity(schema.entities.find((x) => x.key === e.target.value)!, e.target.value)}>
                  {schema.entities.map((e) => <MenuItem key={e.key} value={e.key}>{t(`query.entities.${e.key}`, { defaultValue: e.key })}</MenuItem>)}
                </Select>
              </FormControl>
              <ToggleButtonGroup size="small" exclusive value={match} onChange={(_, v) => v && setMatch(v)}>
                <ToggleButton value="all">{t('query.match_all')}</ToggleButton>
                <ToggleButton value="any">{t('query.match_any')}</ToggleButton>
              </ToggleButtonGroup>
            </Box>

            {conds.length === 0 && <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>{t('query.no_conditions')}</Typography>}
            {conds.map((c, i) => {
              const f = fields[c.field];
              return (
                <Box key={i} sx={{ display: 'flex', gap: 1, alignItems: 'flex-start', flexWrap: 'wrap', mb: 1.5 }}>
                  <FormControl size="small" sx={{ minWidth: 220 }}>
                    <InputLabel>{t('query.field')}</InputLabel>
                    <Select label={t('query.field')} value={c.field} onChange={(e) => changeCondition(i, { field: e.target.value })}>
                      {entity.fields.map((x) => <MenuItem key={x.key} value={x.key}>{label(x.key)}</MenuItem>)}
                    </Select>
                  </FormControl>
                  <FormControl size="small" sx={{ minWidth: 180 }}>
                    <InputLabel>{t('query.operator')}</InputLabel>
                    <Select label={t('query.operator')} value={c.operator} onChange={(e) => changeCondition(i, { operator: e.target.value })}>
                      {f.operators.map((o) => <MenuItem key={o} value={o}>{t(`query.operators.${o}`)}</MenuItem>)}
                    </Select>
                  </FormControl>
                  <ValueInput field={f} cond={c} onChange={(value) => changeCondition(i, { value })} />
                  <IconButton aria-label={t('query.remove')} onClick={() => setConds(conds.filter((_, j) => j !== i))}><DeleteIcon /></IconButton>
                </Box>
              );
            })}
            <Button size="small" startIcon={<AddIcon />} onClick={addCondition}>{t('query.add_condition')}</Button>

            <Box sx={{ display: 'flex', gap: 2, flexWrap: 'wrap', alignItems: 'center', mt: 2 }}>
              <FormControl size="small" sx={{ minWidth: 260 }}>
                <InputLabel>{t('query.columns')}</InputLabel>
                <Select
                  multiple label={t('query.columns')} value={columns} renderValue={(v) => v.map(label).join(', ')}
                  onChange={(e) => setColumns(typeof e.target.value === 'string' ? e.target.value.split(',') : e.target.value)}
                >
                  {selectable.map((f) => <MenuItem key={f.key} value={f.key}><Checkbox size="small" checked={columns.includes(f.key)} /><ListItemText primary={label(f.key)} /></MenuItem>)}
                </Select>
              </FormControl>
              <FormControl size="small" sx={{ minWidth: 200 }}>
                <InputLabel>{t('query.sort')}</InputLabel>
                <Select label={t('query.sort')} value={sort} onChange={(e) => setSort(e.target.value)}>
                  {sortable.map((f) => <MenuItem key={f.key} value={f.key}>{label(f.key)}</MenuItem>)}
                </Select>
              </FormControl>
              <ToggleButtonGroup size="small" exclusive value={direction} onChange={(_, v) => v && setDirection(v)}>
                <ToggleButton value="asc">{t('query.asc')}</ToggleButton>
                <ToggleButton value="desc">{t('query.desc')}</ToggleButton>
              </ToggleButtonGroup>
              <Box sx={{ flex: 1 }} />
              <Button variant="contained" disabled={columns.length === 0} onClick={() => run(0)}>{t('query.run')}</Button>
              {schema.can_export && (
                <>
                  <Button startIcon={<DownloadIcon />} disabled={columns.length === 0} onClick={() => exportAs('csv')}>CSV</Button>
                  <Button startIcon={<DownloadIcon />} disabled={columns.length === 0} onClick={() => exportAs('xlsx')}>Excel</Button>
                </>
              )}
            </Box>
          </Paper>
        )}

        {result && (
          <Paper>
            <Typography variant="subtitle2" sx={{ px: 2, pt: 2 }}>{t('query.total', { count: result.total })}</Typography>
            <TableContainer>
              <Table size="small">
                <TableHead>
                  <TableRow>
                    {result.columns.map((c) => <TableCell key={c.key}>{label(c.key)}</TableCell>)}
                    <TableCell>{t('query.tags')}</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {result.rows.length === 0 && <TableRow><TableCell colSpan={result.columns.length + 1}>{t('query.empty')}</TableCell></TableRow>}
                  {result.rows.map((row) => (
                    <TableRow key={row.id} hover>
                      {result.columns.map((c) => <TableCell key={c.key}><Cell type={c.type} value={row.values[c.key]} /></TableCell>)}
                      <TableCell>{row.tags.map((tag) => <Chip key={tag} size="small" label={tag} sx={{ mr: 0.5 }} />)}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </TableContainer>
            <TablePagination
              component="div" count={result.total} page={Math.floor(result.offset / result.limit)} rowsPerPage={result.limit}
              rowsPerPageOptions={[10, 25, 50, 100]}
              onPageChange={(_, page) => run(page * result.limit, result.limit)}
              onRowsPerPageChange={(e) => { const n = Number(e.target.value); setRowsPerPage(n); run(0, n); }}
            />
          </Paper>
        )}
      </Container>
    </PramaLayout>
  );
};
export default QueryBuilderPage;
