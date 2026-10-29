import React, { useEffect, useState } from 'react';
import { Autocomplete, Box, Button, Chip, TextField, Typography } from '@mui/material';
import PersonAddIcon from '@mui/icons-material/PersonAdd';
import { apiClient } from '../services/apiClient';
import { usePermissions } from '../services/permissions';
import { useTranslation } from '../i18n/useTranslation';
import { PartyDialog } from './PartyPicker';

export interface PortalPerson {
  id: string;
  name: string;
  email: string | null;
  active: boolean;
  units: string[];
  external?: boolean;      // persona fisica esterna dell'anagrafica dei soggetti
  tax_id?: string | null;
  legacy?: boolean;        // valore scritto a mano prima che fosse vietato: non identifica nessuno
}

interface PeopleResponse {
  available: boolean;
  people: PortalPerson[];
}

// Una lettura per pagina (il cambio di tenant ricarica la pagina); si rilegge quando si aggiunge una persona esterna.
let cached: Promise<PeopleResponse> | null = null;
const listeners = new Set<(d: PeopleResponse) => void>();
const loadPeople = () => {
  if (!cached) {
    cached = apiClient.get<PeopleResponse>('/api/privacypa/authorizations/people').catch(() => {
      cached = null;
      return { available: false, people: [] };
    });
  }
  return cached;
};
const reloadPeople = async () => {
  cached = null;
  const d = await loadPeople();
  listeners.forEach((l) => l(d));
};

// Persone del Portal (utenti del tenant con le strutture dell'organigramma) e persone esterne dell'anagrafica.
export const usePortalPeople = () => {
  const [data, setData] = useState<PeopleResponse | null>(null);
  useEffect(() => {
    let live = true;
    const update = (d: PeopleResponse) => { if (live) setData(d); };
    listeners.add(update);
    loadPeople().then(update);
    return () => { live = false; listeners.delete(update); };
  }, []);
  const people = data?.people || [];
  const byId: Record<string, PortalPerson> = Object.fromEntries(people.map((p) => [p.id, p]));
  return {
    loading: data === null,
    available: Boolean(data?.available),
    people,
    byId,
    nameOf: (id: string | null | undefined) => (id ? byId[id]?.name || id : '—'),
  };
};

const legacyPerson = (value: string): PortalPerson => ({ id: value, name: value, email: null, active: false, units: [], legacy: true });
const labelOf = (p: PortalPerson) => (p.email ? `${p.name} (${p.email})` : p.name);
const detailOf = (p: PortalPerson) => (p.external ? [p.tax_id && `CF ${p.tax_id}`, p.email] : [p.email, p.units.slice(1).join(' · ')])
  .filter(Boolean).join(' — ');
const matches = (p: PortalPerson, q: string) => [p.name, p.email || '', p.tax_id || '', ...p.units].some((s) => s.toLowerCase().includes(q));

const useGroupOf = () => {
  const { t } = useTranslation('privacypa');
  return (p: PortalPerson) => (p.external ? t('people.person.external_group') : p.units[0] || t('people.person.no_unit'));
};

// Pulsante «Nuova persona esterna»: la registra nell'anagrafica dei soggetti con il codice fiscale e la sceglie.
const AddExternal: React.FC<{ onAdded: (id: string) => void }> = ({ onAdded }) => {
  const { t } = useTranslation('privacypa');
  const { can } = usePermissions();
  const [open, setOpen] = useState(false);
  if (!can('party.create')) return null;
  return (
    <>
      <Button size="small" startIcon={<PersonAddIcon />} onClick={() => setOpen(true)} sx={{ mt: 1, whiteSpace: 'nowrap' }}>
        {t('people.person.add_external')}
      </Button>
      <PartyDialog
        open={open} naturalPerson lockKind defaultType="OTHER" onClose={() => setOpen(false)}
        onSaved={async (party) => { setOpen(false); await reloadPeople(); onAdded(party.id); }}
      />
    </>
  );
};

// Scelta di una persona: dall'organigramma del Portal o tra le persone esterne dell'anagrafica (con codice fiscale).
// Non si scrive un nome a mano: chi manca si registra con «Nuova persona esterna». Il valore è l'id del Portal o del soggetto.
export const PersonField: React.FC<{
  label: string; value: string; required?: boolean; disabled?: boolean; hint?: string; onChange: (value: string) => void;
}> = ({ label, value, required, disabled, hint, onChange }) => {
  const { t } = useTranslation('privacypa');
  const groupOf = useGroupOf();
  const { loading, available, people, byId } = usePortalPeople();
  const person = value ? byId[value] || (loading ? undefined : legacyPerson(value)) : undefined;
  const choices = people.filter((p) => p.active);
  const note = person?.legacy ? t('people.person.legacy', { name: value })
    : !available && !loading ? t('people.person.unavailable') : hint || t('people.person.hint');

  return (
    <Box sx={{ display: 'flex', gap: 1, alignItems: 'flex-start' }}>
      <Autocomplete
        sx={{ flex: 1 }}
        disabled={disabled}
        loading={loading}
        options={choices}
        groupBy={groupOf}
        getOptionLabel={labelOf}
        filterOptions={(options, state) => {
          const q = state.inputValue.trim().toLowerCase();
          return q && !(person && state.inputValue === labelOf(person)) ? options.filter((p) => matches(p, q)) : options;
        }}
        value={person || null}
        onChange={(_, chosen) => onChange(chosen ? chosen.id : '')}
        isOptionEqualToValue={(o, v) => o.id === v.id}
        noOptionsText={t('people.person.no_match')}
        renderOption={(props, p) => (
          <Box component="li" {...props} key={p.id}>
            <Box>
              <Typography variant="body2">{p.name}</Typography>
              <Typography variant="caption" color="text.secondary">{detailOf(p)}</Typography>
            </Box>
          </Box>
        )}
        renderInput={(params) => (
          <TextField {...params} label={label} required={required} error={Boolean(person?.legacy)} helperText={note} />
        )}
      />
      {!disabled && <AddExternal onAdded={onChange} />}
    </Box>
  );
};

// Nome della persona; un valore scritto a mano in passato si mostra segnalato come non identificato.
export const PersonName: React.FC<{ id: string | null | undefined }> = ({ id }) => {
  const { t } = useTranslation('privacypa');
  const { loading, byId } = usePortalPeople();
  if (!id) return <>—</>;
  if (byId[id] || loading) return <>{byId[id]?.name || id}</>;
  return <Box component="span" title={t('people.person.legacy', { name: id })} sx={{ color: 'error.main' }}>{id} ⚠</Box>;
};

// Più persone dal Portal o dall'anagrafica delle persone esterne.
export const PeopleField: React.FC<{
  label: string; value: string[]; required?: boolean; hint?: string; onChange: (value: string[]) => void;
}> = ({ label, value, required, hint, onChange }) => {
  const { t } = useTranslation('privacypa');
  const groupOf = useGroupOf();
  const { loading, available, people, byId } = usePortalPeople();
  const choices = people.filter((p) => p.active);
  const chosen = value.map((id) => byId[id] || legacyPerson(id));
  const legacy = loading ? [] : chosen.filter((p) => p.legacy);
  return (
    <Box sx={{ display: 'flex', gap: 1, alignItems: 'flex-start' }}>
      <Autocomplete
        sx={{ flex: 1 }}
        multiple
        disableCloseOnSelect
        loading={loading}
        options={choices}
        groupBy={groupOf}
        value={chosen}
        getOptionLabel={labelOf}
        isOptionEqualToValue={(o, v) => o.id === v.id}
        filterOptions={(options, state) => {
          const q = state.inputValue.trim().toLowerCase();
          return q ? options.filter((p) => matches(p, q)) : options;
        }}
        noOptionsText={t('people.person.no_match')}
        onChange={(_, list) => onChange(list.map((o) => o.id))}
        renderTags={(list, getTagProps) => list.map((p, i) => (
          <Chip {...getTagProps({ index: i })} key={p.id} size="small" label={p.name} color={p.legacy && !loading ? 'error' : 'default'} />
        ))}
        renderInput={(params) => (
          <TextField {...params} label={label} required={required && value.length === 0} error={legacy.length > 0}
            helperText={legacy.length ? t('people.person.legacy', { name: legacy.map((p) => p.name).join(', ') })
              : !available && !loading ? t('people.person.unavailable') : hint || t('people.person.hint')} />
        )}
      />
      <AddExternal onAdded={(id) => onChange([...value, id])} />
    </Box>
  );
};
