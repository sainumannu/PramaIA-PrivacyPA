import React, { useEffect, useState } from 'react';
import {
  Alert, Box, Button, Checkbox, Chip, Container, Dialog, DialogActions, DialogContent, DialogTitle, FormControl,
  FormControlLabel, InputLabel, MenuItem, OutlinedInput, Paper, Select, Table, TableBody, TableCell, TableHead,
  TableRow, TextField, Typography,
} from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import { PramaLayout } from './PramaLayout';
import { apiClient } from '../services/apiClient';
import { usePermissions } from '../services/permissions';
import { AnnotationsButton } from './AnnotationsPanel';
import { PeopleField, PersonField } from './PortalPeople';
import { MultiPick, SinglePick, UnitPicker } from './UnitPicker';
import { useTranslation } from '../i18n/useTranslation';
import { ErrorBanner } from './ErrorBanner';

export type FieldType = 'text' | 'textarea' | 'date' | 'datetime' | 'number' | 'boolean' | 'tristate' | 'select' | 'multiselect' | 'unit' | 'units' | 'ref' | 'refs' | 'person' | 'people';

export interface FieldSpec {
  name: string;
  type: FieldType;
  required?: boolean;
  options?: string[];           // per 'select': i valori; le etichette sono in wf.enum.<enum>.<valore>
  enumName?: string;
  createOnly?: boolean;         // modificabile solo alla creazione
  hint?: string;                // chiave di traduzione di un suggerimento
  // Per 'ref' e 'refs' (scelta multipla): elenco da cui scegliere record (l'endpoint può dipendere dagli altri valori del modulo).
  refEndpoint?: string | ((values: Values) => string | null);
  refLabel?: (row: any) => string;
  dependsOn?: string;           // si svuota quando cambia il campo indicato
  defaultValue?: any;           // valore iniziale in creazione (altrimenti vuoto)
}

export type Values = Record<string, any>;

export interface ActionSpec {
  key: string;
  visible?: (row: any) => boolean;
  permission?: string;
  fields?: FieldSpec[];         // dati da chiedere prima di eseguire
  notes?: 'optional' | 'required';
  color?: 'inherit' | 'success' | 'warning' | 'error' | 'primary';
  run: (row: any, values: Values) => Promise<void>;
}

interface Unit { id: string; name: string }

const toLocalInput = (iso: string | null | undefined) => (iso ? iso.slice(0, 16) : '');
const fromLocalInput = (value: string) => (value ? (value.length === 16 ? `${value}:00` : value) : null);

const isList = (type: FieldType) => type === 'units' || type === 'multiselect' || type === 'refs' || type === 'people';

export const emptyFor = (fields: FieldSpec[]): Values =>
  Object.fromEntries(fields.map((f) => [f.name, f.defaultValue !== undefined ? f.defaultValue : f.type === 'boolean' ? false : isList(f.type) ? [] : '']));

export const valuesFromRow = (fields: FieldSpec[], row: any): Values =>
  Object.fromEntries(fields.map((f) => {
    const v = row?.[f.name];
    if (f.type === 'datetime') return [f.name, toLocalInput(v)];
    if (f.type === 'tristate') return [f.name, v === true ? 'yes' : v === false ? 'no' : ''];
    if (isList(f.type)) return [f.name, v || []];
    if (f.type === 'boolean') return [f.name, Boolean(v)];
    return [f.name, v ?? ''];
  }));

// Converte i valori del modulo nel corpo della richiesta (vuoto -> null, tri-stato -> booleano, ora locale -> ISO).
export const bodyFromValues = (fields: FieldSpec[], values: Values): Values =>
  Object.fromEntries(fields.map((f) => {
    const v = values[f.name];
    if (f.type === 'datetime') return [f.name, fromLocalInput(v)];
    if (f.type === 'tristate') return [f.name, v === 'yes' ? true : v === 'no' ? false : null];
    if (f.type === 'number') return [f.name, v === '' || v == null ? null : Number(v)];
    if (f.type === 'boolean' || isList(f.type)) return [f.name, v];
    return [f.name, typeof v === 'string' ? (v.trim() === '' ? null : v.trim()) : v];
  }));

// Record dell'endpoint di un campo 'ref'/'refs'; si ricarica se l'endpoint dipende da altri campi.
const useRefItems = (field: FieldSpec, values: Values) => {
  const endpoint = typeof field.refEndpoint === 'function' ? field.refEndpoint(values) : field.refEndpoint || null;
  const [items, setItems] = useState<{ id: string; label: string }[]>([]);
  useEffect(() => {
    let live = true;
    setItems([]);
    if (!endpoint) return undefined;
    apiClient.get<any[]>(endpoint)
      .then((rows) => {
        if (live) setItems(rows.map((r) => ({ id: r.id, label: field.refLabel ? field.refLabel(r) : r.name || r.title || r.id })));
      })
      .catch(() => undefined);
    return () => { live = false; };
  }, [endpoint]);
  return { endpoint, items };
};

// Elenco di record (trattamenti, sistemi, profili...) da cui scegliere, con ricerca.
const RefSelect: React.FC<{
  field: FieldSpec; label: string; values: Values; disabled: boolean; onChange: (value: string) => void;
}> = ({ field, label, values, disabled, onChange }) => {
  const { endpoint, items } = useRefItems(field, values);
  return (
    <SinglePick label={label} items={items} value={values[field.name] ?? ''} required={field.required}
      disabled={disabled || !endpoint} onChange={onChange} />
  );
};

// Più record dello stesso elenco (categorie di dati, trattamenti, corsi...), con ricerca.
const RefMultiSelect: React.FC<{
  field: FieldSpec; label: string; values: Values; disabled: boolean; onChange: (value: string[]) => void;
}> = ({ field, label, values, disabled, onChange }) => {
  const { endpoint, items } = useRefItems(field, values);
  return <MultiPick label={label} items={items} value={values[field.name] || []} disabled={disabled || !endpoint} onChange={onChange} />;
};

export const FormFields: React.FC<{
  fields: FieldSpec[]; values: Values; onChange: (v: Values) => void; units: Unit[]; editing?: boolean;
  onUnitAdded?: () => void;                     // una struttura del Portal è stata aggiunta a PrivacyPA
}> = ({ fields, values, onChange, units, editing, onUnitAdded }) => {
  const { t } = useTranslation('privacypa');
  const set = (name: string, value: any) => {
    const next = { ...values, [name]: value };
    fields.forEach((f) => { if (f.dependsOn === name) next[f.name] = ''; });
    onChange(next);
  };
  return (
    <>
      {fields.map((f) => {
        const label = t(`wf.fields.${f.name}`);
        const disabled = Boolean(editing && f.createOnly);
        const hint = f.hint ? t(f.hint) : undefined;
        if (f.type === 'boolean') {
          return <FormControlLabel key={f.name} control={<Checkbox checked={Boolean(values[f.name])} disabled={disabled} onChange={(e) => set(f.name, e.target.checked)} />} label={label} />;
        }
        if (f.type === 'unit') {
          return <UnitPicker key={f.name} label={label} value={values[f.name] ?? ''} required={f.required} disabled={disabled} onChange={(v) => set(f.name, v)} onUnitAdded={onUnitAdded} />;
        }
        if (f.type === 'select' || f.type === 'tristate') {
          const items: { value: string; label: string }[] =
            f.type === 'tristate' ? [
                { value: '', label: t('wf.tristate.undecided') }, { value: 'yes', label: t('wf.tristate.yes') }, { value: 'no', label: t('wf.tristate.no') },
              ]
                : (f.options || []).map((o) => ({ value: o, label: t(`wf.enum.${f.enumName || f.name}.${o}`) }));
          return (
            <FormControl key={f.name} fullWidth disabled={disabled} required={f.required}>
              <InputLabel>{label}</InputLabel>
              <Select label={label} value={values[f.name] ?? ''} onChange={(e) => set(f.name, e.target.value)}>
                {f.type !== 'tristate' && !f.required && <MenuItem value="">—</MenuItem>}
                {items.map((i) => <MenuItem key={i.value} value={i.value}>{i.label}</MenuItem>)}
              </Select>
            </FormControl>
          );
        }
        if (f.type === 'people') {
          return <PeopleField key={f.name} label={label} value={values[f.name] || []} required={f.required} hint={hint} onChange={(v) => set(f.name, v)} />;
        }
        if (f.type === 'person') {
          return <PersonField key={f.name} label={label} value={values[f.name] ?? ''} required={f.required} disabled={disabled} hint={hint} onChange={(v) => set(f.name, v)} />;
        }
        if (f.type === 'ref') {
          return <RefSelect key={f.name} field={f} label={label} values={values} disabled={disabled} onChange={(v) => set(f.name, v)} />;
        }
        if (f.type === 'refs') {
          return <RefMultiSelect key={f.name} field={f} label={label} values={values} disabled={disabled} onChange={(v) => set(f.name, v)} />;
        }
        if (f.type === 'multiselect') {
          const labelOf = (o: string) => t(`wf.enum.${f.enumName || f.name}.${o}`);
          return (
            <FormControl key={f.name} fullWidth required={f.required}>
              <InputLabel>{label}</InputLabel>
              <Select
                multiple label={label} value={values[f.name] || []} input={<OutlinedInput label={label} />}
                onChange={(e) => set(f.name, typeof e.target.value === 'string' ? e.target.value.split(',') : e.target.value)}
                renderValue={(chosen) => (chosen as string[]).map(labelOf).join(', ')}
              >
                {(f.options || []).map((o) => <MenuItem key={o} value={o}>{labelOf(o)}</MenuItem>)}
              </Select>
            </FormControl>
          );
        }
        if (f.type === 'units') {
          return (
            <FormControl key={f.name} fullWidth required={f.required}>
              <InputLabel>{label}</InputLabel>
              <Select
                multiple label={label} value={values[f.name] || []} input={<OutlinedInput label={label} />}
                onChange={(e) => set(f.name, typeof e.target.value === 'string' ? e.target.value.split(',') : e.target.value)}
                renderValue={(ids) => units.filter((u) => (ids as string[]).includes(u.id)).map((u) => u.name).join(', ')}
              >
                {units.map((u) => <MenuItem key={u.id} value={u.id}>{u.name}</MenuItem>)}
              </Select>
            </FormControl>
          );
        }
        return (
          <TextField
            key={f.name} label={label} value={values[f.name] ?? ''} required={f.required} disabled={disabled} helperText={hint}
            type={f.type === 'date' ? 'date' : f.type === 'datetime' ? 'datetime-local' : f.type === 'number' ? 'number' : 'text'}
            multiline={f.type === 'textarea'} minRows={f.type === 'textarea' ? 3 : undefined}
            InputLabelProps={f.type === 'date' || f.type === 'datetime' ? { shrink: true } : undefined}
            onChange={(e) => set(f.name, e.target.value)}
          />
        );
      })}
    </>
  );
};

export const missingRequired = (fields: FieldSpec[], values: Values) =>
  fields.some((f) => f.required && (values[f.name] === '' || values[f.name] == null || (Array.isArray(values[f.name]) && values[f.name].length === 0)));

interface Props {
  page: string;                                   // chiave in wf.pages.<page>.{title,subtitle}
  icon: React.ReactNode;
  endpoint: string;
  perm: string;                                   // prefisso permessi (es. 'breach')
  permissions?: { create?: string; edit?: string; delete?: string };   // codici diversi da `<perm>.<azione>`
  fields: FieldSpec[];
  columns: { key: string; render: (row: any, units: Unit[]) => React.ReactNode }[];
  actions?: ActionSpec[];
  canEditRow?: (row: any) => boolean;
  toBody?: (values: Values, editing: any | null) => Values;
  detail?: (row: any, reload: () => Promise<void>, units: Unit[]) => React.ReactNode;
  allowDelete?: boolean;
  annotate?: string;                              // tipo di elemento: aggiunge il pulsante di tag, campi custom e commenti
}

// Pagina elenco + creazione/modifica + azioni con dati aggiuntivi, per le entità del workflow di conformità.
export const EntityCrudPage: React.FC<Props> = ({
  page, icon, endpoint, perm, permissions = {}, fields, columns, actions = [], canEditRow, toBody, detail, allowDelete, annotate,
}) => {
  const createPermission = permissions.create || `${perm}.create`;
  const editPermission = permissions.edit || `${perm}.edit`;
  const deletePermission = permissions.delete || `${perm}.delete`;
  const { t } = useTranslation('privacypa');
  const { can } = usePermissions();
  const [rows, setRows] = useState<any[]>([]);
  const [units, setUnits] = useState<Unit[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [dialogError, setDialogError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<any | null>(null);
  const [values, setValues] = useState<Values>(emptyFor(fields));
  const [running, setRunning] = useState<{ action: ActionSpec; row: any } | null>(null);
  const [actionValues, setActionValues] = useState<Values>({});
  const [selected, setSelected] = useState<any | null>(null);

  const load = async () => {
    try {
      setRows(await apiClient.get<any[]>(endpoint));
    } catch (err: any) {
      setError(err.message);
    }
  };
  const loadUnits = () => apiClient.get<Unit[]>('/api/privacypa/organizational-units').then(setUnits).catch(() => undefined);
  useEffect(() => {
    load();
    loadUnits();
  }, [endpoint]);

  const reloadAndRefreshSelected = async () => {
    const fresh = await apiClient.get<any[]>(endpoint);
    setRows(fresh);
    setSelected((cur: any) => (cur ? fresh.find((r) => r.id === cur.id) || null : cur));
  };

  const openDialog = (row: any | null) => {
    setEditing(row);
    setDialogError(null);
    setValues(row ? valuesFromRow(fields, row) : emptyFor(fields));
    setOpen(true);
  };

  const save = async () => {
    // In modifica i campi `createOnly` non si inviano; il PATCH sostituisce il resto del record.
    const usable = editing ? fields.filter((f) => !f.createOnly) : fields;
    let body = bodyFromValues(usable, values);
    if (toBody) body = toBody(body, editing);
    try {
      if (editing) await apiClient.patch(`${endpoint}/${editing.id}`, body, { version: editing.row_version });
      else await apiClient.post(endpoint, body);
      setOpen(false);
      await reloadAndRefreshSelected();
    } catch (err: any) {
      setDialogError(err.message);
    }
  };

  const remove = async (row: any) => {
    if (!window.confirm(t('wf.confirm_delete'))) return;
    try {
      await apiClient.delete(`${endpoint}/${row.id}`);
      setSelected(null);
      await reloadAndRefreshSelected();
    } catch (err: any) {
      setError(err.message);
    }
  };

  const start = (action: ActionSpec, row: any) => {
    if (!action.fields && !action.notes) {
      action.run(row, {}).then(reloadAndRefreshSelected).catch((err) => setError(err.message));
      return;
    }
    setRunning({ action, row });
    setActionValues(emptyFor([...(action.fields || []), ...(action.notes ? [{ name: 'notes', type: 'textarea' as FieldType }] : [])]));
    setDialogError(null);
  };

  const confirmAction = async () => {
    if (!running) return;
    const { action, row } = running;
    const body = bodyFromValues([...(action.fields || []), { name: 'notes', type: 'textarea' as FieldType }], actionValues);
    try {
      await action.run(row, body);
      setRunning(null);
      setInfo(t('wf.done'));
      await reloadAndRefreshSelected();
    } catch (err: any) {
      setDialogError(err.message);
    }
  };

  const rowActions = (row: any) => actions.filter((a) => (!a.visible || a.visible(row)) && (!a.permission || can(a.permission)));
  const editable = (row: any) => can(editPermission) && (!canEditRow || canEditRow(row));
  const runningFields = running ? [...(running.action.fields || []), ...(running.action.notes ? [{ name: 'notes', type: 'textarea' as FieldType, required: running.action.notes === 'required' }] : [])] : [];

  return (
    <PramaLayout pageTitle={t(`wf.pages.${page}.title`)}>
      <Container maxWidth="xl">
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
          {icon}
          <Typography variant="h4" fontWeight={600}>{t(`wf.pages.${page}.title`)}</Typography>
          <Box sx={{ flex: 1 }} />
          {can(createPermission) && <Button variant="contained" startIcon={<AddIcon />} onClick={() => openDialog(null)}>{t('wf.add')}</Button>}
        </Box>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>{t(`wf.pages.${page}.subtitle`)}</Typography>
        <ErrorBanner message={error} onClose={() => setError(null)} />
        {info && <Alert severity="success" sx={{ mb: 2 }} onClose={() => setInfo(null)}>{info}</Alert>}

        <Paper sx={{ overflowX: 'auto' }}>
          <Table size="small">
            <TableHead>
              <TableRow>
                {columns.map((c) => <TableCell key={c.key}>{t(`wf.columns.${c.key}`)}</TableCell>)}
                <TableCell />
              </TableRow>
            </TableHead>
            <TableBody>
              {rows.length === 0 && <TableRow><TableCell colSpan={columns.length + 1}><Typography variant="body2" color="text.secondary">{t('wf.empty')}</Typography></TableCell></TableRow>}
              {rows.map((row) => (
                <TableRow key={row.id} hover>
                  {columns.map((c) => <TableCell key={c.key}>{c.render(row, units)}</TableCell>)}
                  <TableCell align="right" sx={{ whiteSpace: 'nowrap' }}>
                    {annotate && <AnnotationsButton entityType={annotate} entityId={row.id} title={row.code} />}
                    {detail && <Button size="small" onClick={() => setSelected(row)}>{t('wf.open')}</Button>}
                    {editable(row) && <Button size="small" onClick={() => openDialog(row)}>{t('wf.edit')}</Button>}
                    {rowActions(row).map((a) => (
                      <Button key={a.key} size="small" color={a.color || 'primary'} onClick={() => start(a, row)}>{t(`wf.actions.${a.key}`)}</Button>
                    ))}
                    {allowDelete && can(deletePermission) && <Button size="small" color="error" onClick={() => remove(row)}>{t('wf.delete')}</Button>}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Paper>
      </Container>

      <Dialog open={open} onClose={() => setOpen(false)} maxWidth="sm" fullWidth>
        <DialogTitle>{editing ? t('wf.edit') : t('wf.add')}</DialogTitle>
        <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 2 }}>
          {dialogError && <Alert severity="error">{dialogError}</Alert>}
          <FormFields fields={fields} values={values} onChange={setValues} units={units} editing={Boolean(editing)} onUnitAdded={loadUnits} />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setOpen(false)}>{t('common.cancel')}</Button>
          <Button variant="contained" onClick={save} disabled={missingRequired(fields.filter((f) => !(editing && f.createOnly)), values)}>{t('common.save')}</Button>
        </DialogActions>
      </Dialog>

      <Dialog open={Boolean(running)} onClose={() => setRunning(null)} maxWidth="sm" fullWidth>
        <DialogTitle>{running ? t(`wf.actions.${running.action.key}`) : ''}</DialogTitle>
        <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 2 }}>
          {dialogError && <Alert severity="error">{dialogError}</Alert>}
          <FormFields fields={runningFields} values={actionValues} onChange={setActionValues} units={units} onUnitAdded={loadUnits} />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setRunning(null)}>{t('common.cancel')}</Button>
          <Button variant="contained" onClick={confirmAction} disabled={missingRequired(runningFields, actionValues)}>{t('wf.confirm')}</Button>
        </DialogActions>
      </Dialog>

      {detail && (
        <Dialog open={Boolean(selected)} onClose={() => setSelected(null)} maxWidth="md" fullWidth>
          <DialogTitle>{selected?.code} {selected?.title && `— ${selected.title}`}</DialogTitle>
          <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 1.5, pt: 2 }}>
            {selected && detail(selected, reloadAndRefreshSelected, units)}
          </DialogContent>
          <DialogActions><Button onClick={() => setSelected(null)}>{t('wf.close')}</Button></DialogActions>
        </Dialog>
      )}
    </PramaLayout>
  );
};

export const StatusChip: React.FC<{ enumName: string; value: string | null; color?: 'default' | 'success' | 'warning' | 'error' | 'info' }> = ({ enumName, value, color }) => {
  const { t } = useTranslation('privacypa');
  return value ? <Chip size="small" color={color || 'default'} label={t(`wf.enum.${enumName}.${value}`)} /> : <>—</>;
};

export const withVersion = (row: any) => ({ version: row.row_version });
export const postAction = (endpoint: string, suffix: string) => (row: any, body: Values) =>
  apiClient.post(`${endpoint}/${row.id}${suffix}`, body, withVersion(row)).then(() => undefined);
