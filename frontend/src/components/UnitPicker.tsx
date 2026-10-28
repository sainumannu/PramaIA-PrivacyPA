import React, { useEffect, useMemo, useState } from 'react';
import { Autocomplete, Box, TextField, Typography } from '@mui/material';
import { apiClient } from '../services/apiClient';
import { useTranslation } from '../i18n/useTranslation';

interface Unit { id: string; code: string; name: string; parent_id: string | null; active?: boolean; portal_node_id?: string | null }
interface PortalNode { id: string; code: string | null; name: string; path: string; unit_id: string | null }

type Option =
  | { kind: 'unit'; id: string; code: string; label: string }
  | { kind: 'portal'; id: string; code: string | null; label: string };

const UNITS = '/api/privacypa/organizational-units';

// Percorso «Area › Settore › Ufficio» dalla catena dei padri.
const pathOf = (unit: Unit, byId: Record<string, Unit>) => {
  const names: string[] = [];
  let current: Unit | undefined = unit;
  const seen = new Set<string>();
  while (current && !seen.has(current.id)) {
    seen.add(current.id);
    names.unshift(current.name);
    current = current.parent_id ? byId[current.parent_id] : undefined;
  }
  return names.join(' › ');
};

// Struttura organizzativa: si cerca tra quelle di PrivacyPA e dell'organigramma del Portal. Una struttura del Portal non
// ancora presente viene aggiunta a PrivacyPA (con quelle sopra) quando la si sceglie.
export const UnitPicker: React.FC<{
  label: string; value: string; required?: boolean; disabled?: boolean;
  onChange: (unitId: string) => void; onUnitAdded?: () => void;
}> = ({ label, value, required, disabled, onChange, onUnitAdded }) => {
  const { t } = useTranslation('privacypa');
  const [units, setUnits] = useState<Unit[]>([]);
  const [nodes, setNodes] = useState<PortalNode[]>([]);
  const [portalUp, setPortalUp] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    const [unitList, portal] = await Promise.all([
      apiClient.get<Unit[]>(UNITS).catch(() => [] as Unit[]),
      apiClient.get<{ available: boolean; nodes: PortalNode[] }>(`${UNITS}/portal`).catch(() => ({ available: false, nodes: [] })),
    ]);
    setUnits(unitList);
    setNodes(portal.nodes);
    setPortalUp(portal.available);
  };
  useEffect(() => { load(); }, []);

  const options = useMemo<Option[]>(() => {
    const byId = Object.fromEntries(units.map((u) => [u.id, u]));
    const own: Option[] = units
      .filter((u) => u.active !== false || u.id === value)
      .map((u) => ({ kind: 'unit' as const, id: u.id, code: u.code, label: pathOf(u, byId) }))
      .sort((a, b) => a.label.localeCompare(b.label));
    const fromPortal: Option[] = nodes
      .filter((n) => !n.unit_id)
      .map((n) => ({ kind: 'portal' as const, id: n.id, code: n.code, label: n.path }))
      .sort((a, b) => a.label.localeCompare(b.label));
    return [...own, ...fromPortal];
  }, [units, nodes, value]);

  const selected = options.find((o) => o.kind === 'unit' && o.id === value) || null;

  const choose = async (option: Option | null) => {
    setError(null);
    if (!option) { onChange(''); return; }
    if (option.kind === 'unit') { onChange(option.id); return; }
    setBusy(true);
    try {
      const unit = await apiClient.post<Unit>(`${UNITS}/from-portal`, { node_id: option.id });
      await load();
      onUnitAdded?.();
      onChange(unit.id);
    } catch (err: any) {
      setError(err.message || t('unit_picker.add_failed'));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Autocomplete
      options={options}
      value={selected}
      disabled={disabled || busy}
      loading={busy}
      groupBy={(o) => (o.kind === 'unit' ? t('unit_picker.privacypa') : t('unit_picker.portal'))}
      getOptionLabel={(o) => o.label}
      isOptionEqualToValue={(o, v) => o.kind === v.kind && o.id === v.id}
      filterOptions={(list, state) => {
        const q = state.inputValue.trim().toLowerCase();
        return q ? list.filter((o) => `${o.label} ${o.code || ''}`.toLowerCase().includes(q)) : list;
      }}
      onChange={(_, option) => choose(option)}
      renderOption={(props, o) => (
        <Box component="li" {...props} key={`${o.kind}-${o.id}`}>
          <Box>
            <Typography variant="body2">{o.label}</Typography>
            {o.code && <Typography variant="caption" color="text.secondary">{o.code}</Typography>}
          </Box>
        </Box>
      )}
      renderInput={(params) => (
        <TextField
          {...params} label={label} required={required} error={Boolean(error)}
          helperText={error || (!portalUp ? t('unit_picker.portal_unavailable') : undefined)}
        />
      )}
    />
  );
};

// Più voci di un elenco (basi giuridiche, interessati, sistemi...), con ricerca; le scelte restano visibili come etichette.
export const MultiPick: React.FC<{
  label: string; items: { id: string; label: string }[]; value: string[]; onChange: (ids: string[]) => void; helperText?: string;
  disabled?: boolean;
}> = ({ label, items, value, onChange, helperText, disabled }) => {
  const byId = useMemo(() => Object.fromEntries(items.map((i) => [i.id, i])), [items]);
  const chosen = value.map((id) => byId[id] || { id, label: id });
  return (
    <Autocomplete
      multiple
      disableCloseOnSelect
      disabled={disabled}
      options={items}
      value={chosen}
      getOptionLabel={(o) => o.label}
      isOptionEqualToValue={(o, v) => o.id === v.id}
      onChange={(_, list) => onChange(list.map((i) => i.id))}
      renderInput={(params) => <TextField {...params} label={label} helperText={helperText} />}
    />
  );
};

// Una voce di un elenco di record (fornitore, trattamento, sistema...), con ricerca.
export const SinglePick: React.FC<{
  label: string; items: { id: string; label: string }[]; value: string; onChange: (id: string) => void;
  required?: boolean; disabled?: boolean; helperText?: string;
}> = ({ label, items, value, onChange, required, disabled, helperText }) => {
  const selected = value ? items.find((i) => i.id === value) || { id: value, label: value } : null;
  return (
    <Autocomplete
      options={items}
      value={selected}
      disabled={disabled}
      getOptionLabel={(o) => o.label}
      isOptionEqualToValue={(o, v) => o.id === v.id}
      onChange={(_, item) => onChange(item ? item.id : '')}
      renderInput={(params) => <TextField {...params} label={label} required={required} helperText={helperText} />}
    />
  );
};
