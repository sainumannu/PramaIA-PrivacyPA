import React, { useEffect, useMemo, useState } from 'react';
import {
  Alert, Autocomplete, Box, Button, Chip, Dialog, DialogActions, DialogContent, DialogTitle, FormControl,
  FormControlLabel, InputLabel, Link, MenuItem, Radio, RadioGroup, Select, TextField, ToggleButton, ToggleButtonGroup,
  Typography, useMediaQuery,
} from '@mui/material';
import { useTheme } from '@mui/material/styles';
import AddIcon from '@mui/icons-material/Add';
import { Link as RouterLink } from 'react-router-dom';
import { apiClient } from '../services/apiClient';
import { usePermissions } from '../services/permissions';
import { useTranslation } from '../i18n/useTranslation';

export interface Appointment {
  start: string | null; until: string | null; source: 'CONTRACT' | 'DATE' | null;
  contract_id: string | null; contract_code: string | null; contract_title: string | null;
  ceased_on: string | null; cessation_note: string | null;
  state: 'ACTIVE' | 'EXPIRING' | 'CEASED' | 'UNDEFINED';
}
export interface PartyRef {
  id: string; code: string; legal_name: string; party_type: string; row_version?: number;
  natural_person?: boolean; identified?: boolean; appointment?: Appointment | null;
  tax_id?: string | null; vat_id?: string | null; pec?: string | null; email?: string | null;
  country_code?: string | null; website?: string | null; notes?: string | null;
  processor_profile?: { party_id: string; [field: string]: unknown } | null;
}
export interface AgreementRef {
  id: string; party_id: string; status: string; effective_from?: string | null; expiry_date?: string | null;
}
interface ContractRef { id: string; code: string; title: string; end_date: string | null }

export const PARTY_TYPES = ['PROCESSOR', 'JOINT_CONTROLLER', 'RECIPIENT', 'SUPPLIER', 'PUBLIC_BODY', 'OTHER'];
// Responsabili e contitolari hanno un incarico a termine
export const APPOINTED_TYPES = ['PROCESSOR', 'JOINT_CONTROLLER'];

export const isArchived = (p: PartyRef) => p.appointment?.state === 'CEASED';

const pad = (n: number) => String(n).padStart(2, '0');
const localIso = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;   // data locale, non UTC

// Stessa regola dei controlli (agreement_is_valid): atto firmato, già in vigore e non scaduto
export function hasValidAgreement(partyId: string, agreements: AgreementRef[]): boolean {
  const today = localIso(new Date());
  return agreements.some((a) => a.party_id === partyId && a.status === 'SIGNED'
    && (!a.effective_from || a.effective_from <= today) && (!a.expiry_date || a.expiry_date >= today));
}

// ---------------------------------------------------------------- codice fiscale (come backend/utils/tax_codes.py)

const PERSON_CF = /^[A-Z]{6}[0-9LMNPQRSTUV]{2}[ABCDEHLMPRST][0-9LMNPQRSTUV]{2}[A-Z][0-9LMNPQRSTUV]{3}[A-Z]$/;
const ODD = [1, 0, 5, 7, 9, 13, 15, 17, 19, 21, 2, 4, 18, 20, 11, 3, 6, 8, 12, 14, 16, 10, 22, 25, 24, 23];
export const normalizeCode = (v: string) => v.replace(/[\s.-]/g, '').toUpperCase();

export function isPersonCode(v: string): boolean {
  if (!PERSON_CF.test(v)) return false;
  let total = 0;
  for (let i = 0; i < 15; i += 1) {
    const ch = v[i];
    const n = /\d/.test(ch) ? Number(ch) : ch.charCodeAt(0) - 65;
    total += i % 2 === 0 ? ODD[n] : n;
  }
  return String.fromCharCode(65 + (total % 26)) === v[15];
}

export function isNumericCode(v: string): boolean {
  if (!/^\d{11}$/.test(v) || v === '00000000000') return false;
  let total = 0;
  for (let i = 0; i < 10; i += 1) {
    let d = Number(v[i]);
    if (i % 2) { d *= 2; if (d > 9) d -= 9; }
    total += d;
  }
  return (10 - (total % 10)) % 10 === Number(v[10]);
}

// Errore da mostrare sotto il campo, o null se gli identificativi bastano
export function identifierError(country: string, natural: boolean, taxId: string, vatId: string, t: (k: string) => string): string | null {
  const cf = normalizeCode(taxId);
  let vat = normalizeCode(vatId);
  if ((country || 'IT').toUpperCase() !== 'IT') return cf || vat ? null : t('parties.identity.foreign_required');
  if (!cf) return t('parties.identity.cf_required');
  if (natural ? !isPersonCode(cf) : !(isNumericCode(cf) || isPersonCode(cf))) {
    return t(natural ? 'parties.identity.cf_person_invalid' : 'parties.identity.cf_entity_invalid');
  }
  if (vat.startsWith('IT')) vat = vat.slice(2);
  if (vat && !isNumericCode(vat)) return t('parties.identity.vat_invalid');
  return null;
}

const identifiers = (p: PartyRef) => [
  p.tax_id && `CF ${p.tax_id}`, p.vat_id && p.vat_id !== p.tax_id && `P.IVA ${p.vat_id}`, p.pec && `PEC ${p.pec}`,
].filter(Boolean).join(' · ');

const addMonths = (iso: string, months: number) => {
  const d = new Date(`${iso}T00:00:00`);
  d.setMonth(d.getMonth() + months);
  d.setDate(d.getDate() - 1);   // un anno dal 1° gennaio finisce il 31 dicembre
  return localIso(d);
};

// Stato dell'incarico come etichetta colorata
export const AppointmentChip: React.FC<{ appointment?: Appointment | null }> = ({ appointment }) => {
  const { t } = useTranslation('privacypa');
  if (!appointment) return null;
  const color = { ACTIVE: 'success', EXPIRING: 'warning', CEASED: 'default', UNDEFINED: 'error' }[appointment.state] as
    'success' | 'warning' | 'default' | 'error';
  const label = appointment.state === 'CEASED'
    ? t('parties.appointment.ceased_on', { date: appointment.ceased_on || appointment.until })
    : appointment.until ? t('parties.appointment.until', { date: appointment.until }) : t('parties.appointment.state.UNDEFINED');
  return <Chip size="small" color={color} variant={appointment.state === 'ACTIVE' ? 'outlined' : 'filled'} label={label} />;
};

// ---------------------------------------------------------------- dialog del soggetto

type TermMode = 'NEW_CONTRACT' | 'CONTRACT' | 'DATE' | 'DURATION';

// Inserimento o modifica di un soggetto esterno (ente, società o persona fisica), con la durata dell'incarico
// per responsabili e contitolari. Usato dall'anagrafica e, per i soggetti nuovi, dai campi che li scelgono.
export const PartyDialog: React.FC<{
  open: boolean; party?: PartyRef | null; defaultType?: string; naturalPerson?: boolean;
  lockKind?: boolean;   // solo persona fisica o solo ente (es. dai campi persona)
  onClose: () => void; onSaved: (party: PartyRef) => void;
}> = ({ open, party, defaultType = 'PROCESSOR', naturalPerson = false, lockKind = false, onClose, onSaved }) => {
  const { t } = useTranslation('privacypa');
  const fullScreen = useMediaQuery(useTheme().breakpoints.down('sm'));
  const today = localIso(new Date());
  const blank = {
    legal_name: '', party_type: defaultType, natural_person: naturalPerson, tax_id: '', vat_id: '', pec: '', email: '',
    country_code: 'IT', start: today, mode: 'NEW_CONTRACT' as TermMode, end: '', months: 12,
    contract_id: '', contract_title: '', cig: '', contract_end: '',
  };
  const [form, setForm] = useState(blank);
  const [contracts, setContracts] = useState<ContractRef[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const set = (patch: Partial<typeof blank>) => setForm((f) => ({ ...f, ...patch }));

  useEffect(() => {
    if (!open) return;
    setError(null);
    if (!party) { setForm({ ...blank }); setContracts([]); return; }
    const a = party.appointment;
    setForm({
      ...blank,
      legal_name: party.legal_name, party_type: party.party_type, natural_person: Boolean(party.natural_person),
      tax_id: party.tax_id || '', vat_id: party.vat_id || '', pec: party.pec || '', email: party.email || '',
      country_code: party.country_code || '', start: a?.start || '',
      mode: a?.source === 'CONTRACT' ? 'CONTRACT' : a?.source === 'DATE' ? 'DATE' : 'NEW_CONTRACT',
      end: a?.source === 'DATE' ? a.until || '' : '', contract_id: a?.contract_id || '',
    });
    apiClient.get<ContractRef[]>(`/api/privacypa/contracts?party_id=${party.id}`).then(setContracts).catch(() => setContracts([]));
  }, [open, party?.id]); // eslint-disable-line

  const appointed = APPOINTED_TYPES.includes(form.party_type);
  const idError = identifierError(form.country_code, form.natural_person, form.tax_id, form.vat_id, t);
  const end = form.mode === 'DATE' ? form.end
    : form.mode === 'DURATION' ? (form.months > 0 ? addMonths(form.start || today, form.months) : '')
      : form.mode === 'NEW_CONTRACT' ? form.contract_end
        : contracts.find((c) => c.id === form.contract_id)?.end_date || '';

  const save = async () => {
    if (!form.legal_name.trim()) { setError(t('common.fill_required')); return; }
    if (idError) { setError(idError); return; }
    if (appointed) {
      if (form.mode === 'NEW_CONTRACT' && (!form.contract_title.trim() || !form.contract_end)) { setError(t('parties.term.contract_required')); return; }
      if (form.mode === 'CONTRACT' && !form.contract_id) { setError(t('parties.term.choose_contract')); return; }
      if (!end) { setError(t('parties.term.required')); return; }
      if (form.start && end < form.start) { setError(t('parties.term.order')); return; }
    }
    // PATCH = PUT: i campi che il dialog non mostra (sito, note, profilo) si rimandano com'erano
    const { party_id: _pid, ...profile } = (party?.processor_profile || { party_id: '' }) as { party_id: string; [k: string]: unknown };
    const body = {
      legal_name: form.legal_name.trim(), party_type: form.party_type, natural_person: form.natural_person,
      tax_id: normalizeCode(form.tax_id) || null, vat_id: form.natural_person ? null : normalizeCode(form.vat_id) || null,
      pec: form.pec.trim() || null, email: form.email.trim() || null, country_code: form.country_code.trim().toUpperCase() || null,
      website: party?.website ?? null, notes: party?.notes ?? null,
      processor_profile: !appointed ? null : {
        status: 'TO_REVIEW', ...profile,
        appointment_start: form.start || null,
        appointment_end: form.mode === 'DATE' || form.mode === 'DURATION' ? end : null,
        appointment_contract_id: form.mode === 'CONTRACT' ? form.contract_id : null,
      },
      new_contract: appointed && form.mode === 'NEW_CONTRACT'
        ? { title: form.contract_title.trim(), cig: form.cig.trim() || null, start_date: form.start || null, end_date: form.contract_end }
        : null,
    };
    setSaving(true);
    try {
      const saved = party
        ? await apiClient.patch<PartyRef>(`/api/privacypa/parties/${party.id}`, body, { version: party.row_version })
        : await apiClient.post<PartyRef>('/api/privacypa/parties', body);
      onSaved(saved);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth fullScreen={fullScreen}>
      <DialogTitle>{party ? `${party.code} — ${party.legal_name}` : t(form.natural_person ? 'parties.add_person' : 'parties.add')}</DialogTitle>
      <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: '8px !important' }}>
        {error && <Alert severity="error" onClose={() => setError(null)}>{error}</Alert>}
        {!party && <Typography variant="body2" color="text.secondary">{t('parties.new_hint')}</Typography>}
        {!lockKind && <ToggleButtonGroup exclusive size="small" value={form.natural_person ? 'person' : 'entity'}
          onChange={(_, v) => v && set({ natural_person: v === 'person' })}>
          <ToggleButton value="entity">{t('parties.kind.entity')}</ToggleButton>
          <ToggleButton value="person">{t('parties.kind.person')}</ToggleButton>
        </ToggleButtonGroup>}
        <TextField label={t(form.natural_person ? 'parties.person_name' : 'parties.name')} value={form.legal_name} required autoFocus
          onChange={(e) => set({ legal_name: e.target.value })} />
        <FormControl>
          <InputLabel>{t('parties.type')}</InputLabel>
          <Select label={t('parties.type')} value={form.party_type} onChange={(e) => set({ party_type: e.target.value })}>
            {PARTY_TYPES.map((x) => <MenuItem key={x} value={x}>{t(`parties.types.${x}`)}</MenuItem>)}
          </Select>
        </FormControl>
        <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', sm: form.natural_person ? '3fr 1fr' : '2fr 2fr 1fr' }, gap: 2 }}>
          <TextField label={t('parties.tax_code')} value={form.tax_id} required={(form.country_code || 'IT').toUpperCase() === 'IT'}
            error={Boolean(form.tax_id) && Boolean(idError)} inputProps={{ style: { textTransform: 'uppercase' } }}
            helperText={form.tax_id && idError ? idError : t(form.natural_person ? 'parties.identity.cf_person_hint' : 'parties.identity.cf_entity_hint')}
            onChange={(e) => set({ tax_id: e.target.value })} />
          {!form.natural_person && (
            <TextField label={t('parties.vat_id')} value={form.vat_id} onChange={(e) => set({ vat_id: e.target.value })} />
          )}
          <TextField label={t('parties.country')} value={form.country_code} inputProps={{ maxLength: 2 }}
            onChange={(e) => set({ country_code: e.target.value })} />
        </Box>
        <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', sm: '1fr 1fr' }, gap: 2 }}>
          <TextField label="PEC" value={form.pec} onChange={(e) => set({ pec: e.target.value })} />
          <TextField label="Email" value={form.email} onChange={(e) => set({ email: e.target.value })} />
        </Box>

        {appointed && (
          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1.5, p: 2, border: 1, borderColor: 'divider', borderRadius: 1 }}>
            <Typography variant="subtitle2">{t('parties.term.title')}</Typography>
            <Typography variant="caption" color="text.secondary">{t('parties.term.hint')}</Typography>
            <TextField type="date" label={t('parties.term.start')} InputLabelProps={{ shrink: true }} value={form.start}
              onChange={(e) => set({ start: e.target.value })} sx={{ maxWidth: 220 }} />
            <RadioGroup value={form.mode} onChange={(e) => set({ mode: e.target.value as TermMode })}>
              <FormControlLabel value="NEW_CONTRACT" control={<Radio size="small" />} label={t('parties.term.mode.NEW_CONTRACT')} />
              {party && contracts.length > 0 && (
                <FormControlLabel value="CONTRACT" control={<Radio size="small" />} label={t('parties.term.mode.CONTRACT')} />
              )}
              <FormControlLabel value="DURATION" control={<Radio size="small" />} label={t('parties.term.mode.DURATION')} />
              <FormControlLabel value="DATE" control={<Radio size="small" />} label={t('parties.term.mode.DATE')} />
            </RadioGroup>
            {form.mode === 'NEW_CONTRACT' && (
              <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', sm: '2fr 1fr 1fr' }, gap: 2 }}>
                <TextField label={t('parties.term.contract_title')} value={form.contract_title} required
                  onChange={(e) => set({ contract_title: e.target.value })} />
                <TextField label="CIG" value={form.cig} inputProps={{ maxLength: 10 }} onChange={(e) => set({ cig: e.target.value })} />
                <TextField type="date" label={t('parties.term.contract_end')} InputLabelProps={{ shrink: true }} required
                  value={form.contract_end} onChange={(e) => set({ contract_end: e.target.value })} />
              </Box>
            )}
            {form.mode === 'CONTRACT' && (
              <FormControl>
                <InputLabel>{t('parties.term.contract')}</InputLabel>
                <Select label={t('parties.term.contract')} value={form.contract_id} onChange={(e) => set({ contract_id: e.target.value })}>
                  {contracts.map((c) => (
                    <MenuItem key={c.id} value={c.id} disabled={!c.end_date}>
                      {c.code} — {c.title} {c.end_date ? `(${t('parties.appointment.until', { date: c.end_date })})` : `(${t('parties.term.no_end')})`}
                    </MenuItem>
                  ))}
                </Select>
              </FormControl>
            )}
            {form.mode === 'DURATION' && (
              <TextField type="number" label={t('parties.term.months')} value={form.months} inputProps={{ min: 1, max: 240 }}
                onChange={(e) => set({ months: Number(e.target.value) })} sx={{ maxWidth: 220 }} />
            )}
            {form.mode === 'DATE' && (
              <TextField type="date" label={t('parties.term.end')} InputLabelProps={{ shrink: true }} value={form.end}
                onChange={(e) => set({ end: e.target.value })} sx={{ maxWidth: 220 }} />
            )}
            {end && <Typography variant="body2">{t('parties.term.result', { date: end })}</Typography>}
          </Box>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>{t('common.cancel')}</Button>
        <Button variant="contained" onClick={save} disabled={saving}>{t('common.save')}</Button>
      </DialogActions>
    </Dialog>
  );
};

// ---------------------------------------------------------------- scelta di un soggetto

const optionDetail = (p: PartyRef, t: (k: string, o?: Record<string, unknown>) => string) =>
  [p.code, identifiers(p) || t('party_picker.no_identifier'),
    p.appointment?.until && t('parties.appointment.until', { date: p.appointment.until })].filter(Boolean).join(' · ');

const searchParties = (opts: PartyRef[], input: string) => {
  const q = input.trim().toLowerCase();
  return q ? opts.filter((p) => [p.legal_name, p.code, p.tax_id || '', p.vat_id || ''].some((s) => s.toLowerCase().includes(q))) : opts;
};

// Un soggetto dell'anagrafica (fornitore di un contratto, responsabile, sub-responsabile...). Il testo digitato serve
// solo a cercare: uscendo dal campo si sceglie la voce evidenziata; chi manca si registra con «Nuovo soggetto».
export const PartySelect: React.FC<{
  label: string; parties: PartyRef[]; value: string; onChange: (id: string) => void; onPartyAdded: (party: PartyRef) => void;
  required?: boolean; disabled?: boolean; defaultType?: string;
}> = ({ label, parties, value, onChange, onPartyAdded, required, disabled, defaultType = 'SUPPLIER' }) => {
  const { t } = useTranslation('privacypa');
  const { can } = usePermissions();
  const [adding, setAdding] = useState(false);
  const options = useMemo(() => parties.filter((p) => !isArchived(p) || p.id === value)
    .sort((a, b) => a.legal_name.localeCompare(b.legal_name)), [parties, value]);
  const selected = value ? parties.find((p) => p.id === value) || { id: value, code: '', legal_name: value, party_type: 'OTHER' } : null;
  const empty = options.length === 0;

  return (
    <Box sx={{ display: 'flex', gap: 1, alignItems: 'flex-start' }}>
      <Autocomplete
        sx={{ flex: 1 }}
        disabled={disabled}
        autoHighlight
        autoSelect
        options={options}
        value={selected}
        getOptionLabel={(p) => (p.code ? `${p.code} — ${p.legal_name}` : p.legal_name)}
        isOptionEqualToValue={(o, v) => o.id === v.id}
        onChange={(_, p) => onChange(p ? p.id : '')}
        filterOptions={(opts, state) => searchParties(opts, state.inputValue)}
        noOptionsText={t('party_picker.no_match')}
        renderOption={(props, p) => (
          <li {...props} key={p.id}>
            <Box>
              <Typography variant="body2">{p.legal_name}</Typography>
              <Typography variant="caption" color="text.secondary">{optionDetail(p, t)}</Typography>
            </Box>
          </li>
        )}
        renderInput={(params) => (
          <TextField {...params} label={label} required={required} placeholder={empty ? undefined : t('party_picker.search')}
            helperText={selected && identifiers(selected as PartyRef) ? identifiers(selected as PartyRef)
              : t(empty ? 'party_picker.empty_party' : 'party_picker.hint_party')} />
        )}
      />
      {!disabled && can('party.create') && (
        <Button size="small" startIcon={<AddIcon />} onClick={() => setAdding(true)} sx={{ mt: 1, whiteSpace: 'nowrap' }}>
          {t('party_picker.add_party')}
        </Button>
      )}
      <PartyDialog
        open={adding}
        defaultType={defaultType}
        onClose={() => setAdding(false)}
        onSaved={(party) => { setAdding(false); onPartyAdded(party); onChange(party.id); }}
      />
    </Box>
  );
};

// ---------------------------------------------------------------- scelta di responsabili e destinatari

// Responsabili art. 28 o destinatari di un trattamento: si scelgono solo dall'anagrafica dei soggetti esterni,
// mai a testo libero. I soggetti in archivio (incarico cessato) non si propongono, ma restano se già collegati.
export const PartyPicker: React.FC<{
  kind: 'processor' | 'recipient';
  parties: PartyRef[]; agreements: AgreementRef[]; value: string[];
  onChange: (ids: string[]) => void; onPartyAdded: (party: PartyRef) => void;
}> = ({ kind, parties, agreements, value, onChange, onPartyAdded }) => {
  const { t } = useTranslation('privacypa');
  const { can } = usePermissions();
  const [adding, setAdding] = useState(false);
  const byId = useMemo(() => Object.fromEntries(parties.map((p) => [p.id, p])), [parties]);
  const options = useMemo(() => {
    const list = parties.filter((p) => !isArchived(p) && (kind === 'recipient' || p.party_type === 'PROCESSOR'));
    const order = (p: PartyRef) => PARTY_TYPES.indexOf(p.party_type);
    return [...list].sort((a, b) => order(a) - order(b) || a.legal_name.localeCompare(b.legal_name));
  }, [kind, parties]);
  const chosen = value.map((id) => byId[id] || { id, code: '', legal_name: id, party_type: 'OTHER' });
  const ceased = chosen.filter(isArchived);
  const withoutAgreement = kind === 'processor' ? chosen.filter((p) => !isArchived(p) && !hasValidAgreement(p.id, agreements)) : [];
  const label = t(kind === 'processor' ? 'register.fields.processors' : 'register.fields.recipients');
  const empty = options.length === 0;

  return (
    <Box>
      <Box sx={{ display: 'flex', gap: 1, alignItems: 'flex-start' }}>
        <Autocomplete
          multiple
          disableCloseOnSelect
          sx={{ flex: 1 }}
          options={options}
          value={chosen}
          groupBy={kind === 'recipient' ? (p) => t(`parties.types.${p.party_type}`) : undefined}
          getOptionLabel={(p) => p.legal_name}
          isOptionEqualToValue={(o, v) => o.id === v.id}
          onChange={(_, list) => onChange(list.map((p) => p.id))}
          noOptionsText={t('party_picker.no_match')}
          filterOptions={(opts, state) => searchParties(opts, state.inputValue)}
          renderOption={(props, p) => (
            <li {...props} key={p.id}>
              <Box>
                <Typography variant="body2">{p.legal_name}</Typography>
                <Typography variant="caption" color="text.secondary">{optionDetail(p, t)}</Typography>
              </Box>
            </li>
          )}
          renderTags={(list, getTagProps) => list.map((p, i) => {
            const color = isArchived(p) ? 'error' : kind === 'processor' && !hasValidAgreement(p.id, agreements) ? 'warning' : 'default';
            return (
              <Chip {...getTagProps({ index: i })} key={p.id} size="small" label={p.legal_name} color={color}
                title={identifiers(p) || undefined} />
            );
          })}
          renderInput={(params) => (
            <TextField {...params} label={label} placeholder={empty ? undefined : t('party_picker.search')}
              helperText={empty ? t(`party_picker.empty_${kind}`) : t(`party_picker.hint_${kind}`)} />
          )}
        />
        {can('party.create') && (
          <Button size="small" startIcon={<AddIcon />} onClick={() => setAdding(true)} sx={{ mt: 1, whiteSpace: 'nowrap' }}>
            {t(`party_picker.add_${kind}`)}
          </Button>
        )}
      </Box>
      {ceased.length > 0 && (
        <Alert severity="error" sx={{ mt: 1 }}>
          {t('party_picker.ceased', { names: ceased.map((p) => p.legal_name).join(', ') })}
        </Alert>
      )}
      {withoutAgreement.length > 0 && (
        <Alert severity="warning" sx={{ mt: 1 }}>
          {t('party_picker.no_agreement', { names: withoutAgreement.map((p) => p.legal_name).join(', ') })}{' '}
          <Link component={RouterLink} to="/fornitori">{t('party_picker.open_parties')}</Link>
        </Alert>
      )}
      <PartyDialog
        open={adding}
        defaultType={kind === 'processor' ? 'PROCESSOR' : 'RECIPIENT'}
        onClose={() => setAdding(false)}
        onSaved={(party) => {
          setAdding(false);
          onPartyAdded(party);
          onChange([...value, party.id]);
        }}
      />
    </Box>
  );
};
