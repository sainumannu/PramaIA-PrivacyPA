import React, { useEffect, useState } from 'react';
import { Link as RouterLink, useSearchParams } from 'react-router-dom';
import { Box, Button, Container, List, ListItemButton, ListItemText, Paper, TextField, Typography } from '@mui/material';
import SearchIcon from '@mui/icons-material/Search';
import { PramaLayout } from '../components/PramaLayout';
import { apiClient } from '../services/apiClient';
import { useTranslation } from '../i18n/useTranslation';
import { ErrorBanner } from '../components/ErrorBanner';

interface Results {
  query: string;
  processing_activities: { id: string; code: string; title: string; status: string }[];
  assets: { id: string; code: string; name: string }[];
  parties: { id: string; code: string; legal_name: string }[];
  documents: { id: string; code: string; title: string }[];
  tasks: { id: string; code: string; title: string }[];
  dpias: { id: string; code: string; title: string }[];
  transfers: { id: string; code: string; title: string }[];
  risks: { id: string; code: string; title: string }[];
  notices: { id: string; code: string; title: string }[];
  contracts: { id: string; code: string; title: string }[];
  data_breaches: { id: string; code: string; title: string }[];
  projects: { id: string; code: string; title: string }[];
  corrective_actions: { id: string; code: string; title: string }[];
  audits: { id: string; code: string; title: string }[];
  total: number;
}

export const SearchPage: React.FC = () => {
  const { t } = useTranslation('privacypa');
  const [params, setParams] = useSearchParams();
  const [q, setQ] = useState(params.get('q') || '');
  const [results, setResults] = useState<Results | null>(null);
  const [error, setError] = useState<string | null>(null);

  const run = async (term: string) => {
    if (term.trim().length < 2) return;
    try {
      setResults(await apiClient.get<Results>(`/api/privacypa/search?q=${encodeURIComponent(term.trim())}`));
    } catch (e: any) { setError(e.message); }
  };

  useEffect(() => {
    const initial = params.get('q');
    if (initial) run(initial);
  }, []);

  const groups: { key: keyof Results; to: string; label: string }[] = [
    { key: 'processing_activities', to: '/registro', label: t('search.processing') },
    { key: 'assets', to: '/sistemi', label: t('search.assets') },
    { key: 'parties', to: '/fornitori', label: t('search.parties') },
    { key: 'documents', to: '/documenti', label: t('search.documents') },
    { key: 'tasks', to: '/task', label: t('search.tasks') },
    { key: 'dpias', to: '/dpia', label: t('search.dpias') },
    { key: 'transfers', to: '/trasferimenti', label: t('search.transfers') },
    { key: 'risks', to: '/rischi', label: t('search.risks') },
    { key: 'notices', to: '/informative', label: t('search.notices') },
    { key: 'contracts', to: '/filiera', label: t('search.contracts') },
    { key: 'data_breaches', to: '/violazioni', label: t('search.breaches') },
    { key: 'projects', to: '/progetti', label: t('search.projects') },
    { key: 'corrective_actions', to: '/azioni', label: t('search.actions') },
    { key: 'audits', to: '/audit-programmati', label: t('search.audits') },
  ];

  return (
    <PramaLayout pageTitle={t('search.title')}>
      <Container maxWidth="md">
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 2 }}>
          <SearchIcon color="primary" />
          <Typography variant="h4" fontWeight={600}>{t('search.title')}</Typography>
        </Box>
        <Box sx={{ display: 'flex', gap: 1, mb: 3 }}>
          <TextField fullWidth value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => {
            if (e.key === 'Enter') { setParams({ q }); run(q); }
          }} placeholder={t('search.placeholder')} />
          <Button variant="contained" onClick={() => { setParams({ q }); run(q); }}>{t('search.run')}</Button>
        </Box>
        <ErrorBanner message={error} onClose={() => setError(null)} />
        {results && results.total === 0 && <Typography color="text.secondary">{t('search.none')}</Typography>}
        {results && groups.map((g) => {
          const rows = (results[g.key] as any[]) || [];
          if (!rows.length) return null;
          return (
            <Paper key={g.key} sx={{ mb: 2 }}>
              <Typography variant="subtitle2" sx={{ px: 2, pt: 2 }}>{g.label} ({rows.length})</Typography>
              <List>
                {rows.map((row) => (
                  <ListItemButton key={row.id} component={RouterLink} to={g.to}>
                    <ListItemText primary={`${row.code} — ${row.title || row.name || row.legal_name}`} />
                  </ListItemButton>
                ))}
              </List>
            </Paper>
          );
        })}
      </Container>
    </PramaLayout>
  );
};
export default SearchPage;
