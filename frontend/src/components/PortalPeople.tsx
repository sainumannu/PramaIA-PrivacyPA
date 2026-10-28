import React, { useEffect, useState } from 'react';
import { Autocomplete, Box, TextField, Typography } from '@mui/material';
import { apiClient } from '../services/apiClient';
import { useTranslation } from '../i18n/useTranslation';

export interface PortalPerson {
  id: string;
  name: string;
  email: string | null;
  active: boolean;
  units: string[];
}

interface PeopleResponse {
  available: boolean;
  people: PortalPerson[];
}

// Una lettura per pagina: il cambio di tenant ricarica la pagina, quindi l'elenco resta quello dell'ente attivo.
let cached: Promise<PeopleResponse> | null = null;
const loadPeople = () => {
  if (!cached) {
    cached = apiClient.get<PeopleResponse>('/api/privacypa/authorizations/people').catch(() => {
      cached = null;
      return { available: false, people: [] };
    });
  }
  return cached;
};

// Persone del Portal (utenti del tenant con le strutture dell'organigramma) e nome da mostrare per un identificativo.
export const usePortalPeople = () => {
  const [data, setData] = useState<PeopleResponse | null>(null);
  useEffect(() => {
    let live = true;
    loadPeople().then((d) => { if (live) setData(d); });
    return () => { live = false; };
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

const labelOf = (p: PortalPerson) => (p.email ? `${p.name} (${p.email})` : p.name);

// Scelta di una persona dall'organigramma del Portal; si può anche scrivere a mano chi non c'è (es. un esterno).
// Il valore è l'id del Portal per chi è stato scelto dall'elenco, altrimenti il testo scritto.
export const PersonField: React.FC<{
  label: string; value: string; required?: boolean; disabled?: boolean; hint?: string; onChange: (value: string) => void;
}> = ({ label, value, required, disabled, hint, onChange }) => {
  const { t } = useTranslation('privacypa');
  const { loading, available, people, byId } = usePortalPeople();
  const person = value ? byId[value] : undefined;
  const choices = people.filter((p) => p.active);
  const note = !available && !loading
    ? t('people.person.unavailable')
    : value && !person ? t('people.person.not_in_portal') : hint;

  return (
    <Autocomplete
      freeSolo
      disabled={disabled}
      loading={loading}
      options={choices}
      groupBy={(p) => p.units[0] || t('people.person.no_unit')}
      getOptionLabel={(o) => (typeof o === 'string' ? o : labelOf(o))}
      filterOptions={(options, state) => {
        const q = state.inputValue.trim().toLowerCase();
        if (!q || (person && state.inputValue === labelOf(person))) return options;
        return options.filter((p) => [p.name, p.email || '', ...p.units].some((s) => s.toLowerCase().includes(q)));
      }}
      value={person || value || null}
      inputValue={person ? labelOf(person) : value || ''}
      onInputChange={(_, text, reason) => { if (reason === 'input' || reason === 'clear') onChange(text); }}
      onChange={(_, chosen) => onChange(chosen == null ? '' : typeof chosen === 'string' ? chosen : chosen.id)}
      isOptionEqualToValue={(o, v) => typeof v !== 'string' && o.id === v.id}
      renderOption={(props, p) => (
        <Box component="li" {...props} key={p.id}>
          <Box>
            <Typography variant="body2">{p.name}</Typography>
            <Typography variant="caption" color="text.secondary">
              {[p.email, p.units.slice(1).join(' · ')].filter(Boolean).join(' — ')}
            </Typography>
          </Box>
        </Box>
      )}
      renderInput={(params) => <TextField {...params} label={label} required={required} helperText={note} />}
    />
  );
};

// Nome della persona del Portal (l'identificativo se non c'è, es. una persona scritta a mano).
export const PersonName: React.FC<{ id: string | null | undefined }> = ({ id }) => {
  const { nameOf } = usePortalPeople();
  return <>{nameOf(id)}</>;
};

// Più persone dall'organigramma del Portal; si possono aggiungere anche identificativi scritti a mano (Invio).
export const PeopleField: React.FC<{
  label: string; value: string[]; required?: boolean; hint?: string; onChange: (value: string[]) => void;
}> = ({ label, value, required, hint, onChange }) => {
  const { t } = useTranslation('privacypa');
  const { loading, available, people, byId } = usePortalPeople();
  const choices = people.filter((p) => p.active);
  return (
    <Autocomplete
      multiple
      freeSolo
      disableCloseOnSelect
      loading={loading}
      options={choices}
      groupBy={(p) => p.units[0] || t('people.person.no_unit')}
      value={value.map((id) => byId[id] || id)}
      getOptionLabel={(o) => (typeof o === 'string' ? o : labelOf(o))}
      isOptionEqualToValue={(o, v) => typeof v !== 'string' && o.id === v.id}
      filterOptions={(options, state) => {
        const q = state.inputValue.trim().toLowerCase();
        return q ? options.filter((p) => [p.name, p.email || '', ...p.units].some((x) => x.toLowerCase().includes(q))) : options;
      }}
      onChange={(_, list) => onChange(list.map((o) => (typeof o === 'string' ? o.trim() : o.id)).filter(Boolean))}
      renderInput={(params) => (
        <TextField {...params} label={label} required={required && value.length === 0}
          helperText={!available && !loading ? t('people.person.unavailable') : hint} />
      )}
    />
  );
};
