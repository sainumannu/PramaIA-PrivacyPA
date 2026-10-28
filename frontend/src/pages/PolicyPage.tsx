import React, { useEffect, useMemo, useState } from 'react';
import {
  Alert, Box, Button, Checkbox, Chip, Container, FormControlLabel, Paper, Switch, Table, TableBody,
  TableCell, TableContainer, TableHead, TableRow, Typography,
} from '@mui/material';
import AdminPanelSettingsIcon from '@mui/icons-material/AdminPanelSettings';
import { PramaLayout } from '../components/PramaLayout';
import { ConfidentialityPanel } from '../components/ConfidentialityPanel';
import { DeadlinesPanel } from '../components/DeadlinesPanel';
import { apiClient } from '../services/apiClient';
import { refreshPermissions } from '../services/permissions';
import { useTranslation } from '../i18n/useTranslation';
import { ErrorBanner } from '../components/ErrorBanner';

interface PermissionRow {
  code: string;
  module: string;
  action: string;
  locked: boolean;
  default_roles: string[];
  roles: string[];
  overridden: boolean;
}
interface Matrix { row_version: number; roles: string[]; permissions: PermissionRow[] }
interface Segregation { row_version: number; rules: Record<string, boolean>; available: string[] }

// Gli amministratori mantengono sempre ogni permesso: la loro colonna non è modificabile.
const ALWAYS = ['SUPER_ADMIN', 'TENANT_ADMIN'];

export const PolicyPage: React.FC = () => {
  const { t } = useTranslation('privacypa');
  const [matrix, setMatrix] = useState<Matrix | null>(null);
  const [draft, setDraft] = useState<Record<string, string[]>>({});
  const [segregation, setSegregation] = useState<Segregation | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const load = async () => {
    try {
      const [m, s] = await Promise.all([
        apiClient.get<Matrix>('/api/privacypa/policy/permissions'),
        apiClient.get<Segregation>('/api/privacypa/policy/segregation'),
      ]);
      setMatrix(m);
      setSegregation(s);
      setDraft(Object.fromEntries(m.permissions.map((p) => [p.code, p.roles])));
    } catch (err: any) {
      setError(err.message || t('policy.load_failed'));
    }
  };

  useEffect(() => { load(); }, []);

  const changed = useMemo(() => {
    if (!matrix) return [] as PermissionRow[];
    const same = (a: string[], b: string[]) => a.length === b.length && a.every((x) => b.includes(x));
    return matrix.permissions.filter((p) => !same(draft[p.code] || [], p.roles));
  }, [matrix, draft]);

  const toggle = (code: string, role: string, checked: boolean) => {
    const current = new Set(draft[code] || []);
    if (checked) current.add(role); else current.delete(role);
    setDraft({ ...draft, [code]: Array.from(current) });
  };

  const saveMatrix = async () => {
    if (!matrix) return;
    // Si inviano solo i permessi che differiscono dal default: gli altri tornano al default.
    const overrides: Record<string, string[]> = {};
    matrix.permissions.forEach((p) => {
      const chosen = draft[p.code] || [];
      const isDefault = chosen.length === p.default_roles.length && chosen.every((r) => p.default_roles.includes(r));
      if (!isDefault) overrides[p.code] = chosen;
    });
    try {
      await apiClient.put('/api/privacypa/policy/permissions', { overrides }, { version: matrix.row_version });
      setSuccess(t('policy.saved'));
      await Promise.all([load(), refreshPermissions()]);
    } catch (err: any) {
      setError(err.message);
      await load();
    }
  };

  const saveRule = async (rule: string, value: boolean) => {
    if (!segregation) return;
    try {
      const next = await apiClient.put<Segregation>(
        '/api/privacypa/policy/segregation', { [rule]: value }, { version: segregation.row_version },
      );
      setSegregation(next);
      setSuccess(t('policy.saved'));
    } catch (err: any) {
      setError(err.message);
      await load();
    }
  };

  const grouped = useMemo(() => {
    const groups: Record<string, PermissionRow[]> = {};
    (matrix?.permissions || []).forEach((p) => { (groups[p.module] ||= []).push(p); });
    return groups;
  }, [matrix]);

  return (
    <PramaLayout pageTitle={t('policy.title')}>
      <Container maxWidth="xl">
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
          <AdminPanelSettingsIcon color="primary" />
          <Typography variant="h4" fontWeight={600}>{t('policy.title')}</Typography>
        </Box>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>{t('policy.subtitle')}</Typography>
        <ErrorBanner message={error} onClose={() => setError(null)} />
        {success && <Alert severity="success" sx={{ mb: 2 }} onClose={() => setSuccess(null)}>{success}</Alert>}

        {segregation && (
          <Paper sx={{ p: 2, mb: 3 }}>
            <Typography variant="h6" sx={{ mb: 0.5 }}>{t('policy.segregation.title')}</Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>{t('policy.segregation.hint')}</Typography>
            <Box sx={{ display: 'flex', flexDirection: 'column' }}>
              {segregation.available.map((rule) => (
                <FormControlLabel
                  key={rule}
                  control={<Switch checked={Boolean(segregation.rules[rule])} onChange={(e) => saveRule(rule, e.target.checked)} />}
                  label={t(`policy.segregation.rules.${rule}`)}
                />
              ))}
            </Box>
          </Paper>
        )}

        <Paper sx={{ p: 2 }}>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
            <Typography variant="h6" sx={{ flex: 1 }}>{t('policy.matrix.title')}</Typography>
            {changed.length > 0 && (
              <Chip size="small" color="warning" label={t('policy.matrix.unsaved', { count: changed.length })} />
            )}
            <Button disabled={changed.length === 0} onClick={() => matrix && setDraft(Object.fromEntries(matrix.permissions.map((p) => [p.code, p.roles])))}>
              {t('policy.matrix.discard')}
            </Button>
            <Button variant="contained" disabled={changed.length === 0} onClick={saveMatrix}>{t('common.save')}</Button>
          </Box>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>{t('policy.matrix.hint')}</Typography>
          <TableContainer>
            <Table size="small" stickyHeader>
              <TableHead>
                <TableRow>
                  <TableCell sx={{ minWidth: 220 }}>{t('policy.matrix.permission')}</TableCell>
                  {(matrix?.roles || []).map((r) => (
                    <TableCell key={r} align="center" sx={{ fontSize: '0.7rem', whiteSpace: 'nowrap' }}>{t(`policy.roles.${r}`)}</TableCell>
                  ))}
                </TableRow>
              </TableHead>
              <TableBody>
                {Object.entries(grouped).map(([module, rows]) => (
                  <React.Fragment key={module}>
                    <TableRow>
                      <TableCell colSpan={(matrix?.roles.length || 0) + 1} sx={{ bgcolor: 'action.hover', fontWeight: 600 }}>
                        {t(`policy.modules.${module}`)}
                      </TableCell>
                    </TableRow>
                    {rows.map((p) => (
                      <TableRow key={p.code} hover>
                        <TableCell>
                          {t(`policy.actions.${p.action}`)}
                          {p.overridden && <Chip size="small" color="info" variant="outlined" sx={{ ml: 1, height: 18 }} label={t('policy.matrix.custom')} />}
                          {p.locked && <Chip size="small" variant="outlined" sx={{ ml: 1, height: 18 }} label={t('policy.matrix.locked')} />}
                        </TableCell>
                        {(matrix?.roles || []).map((r) => (
                          <TableCell key={r} align="center" padding="checkbox">
                            <Checkbox
                              size="small"
                              checked={(draft[p.code] || []).includes(r)}
                              disabled={p.locked || ALWAYS.includes(r)}
                              onChange={(e) => toggle(p.code, r, e.target.checked)}
                              inputProps={{ 'aria-label': `${p.code} ${r}` }}
                            />
                          </TableCell>
                        ))}
                      </TableRow>
                    ))}
                  </React.Fragment>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
        </Paper>
        <DeadlinesPanel />
        <ConfidentialityPanel />
      </Container>
    </PramaLayout>
  );
};

export default PolicyPage;
