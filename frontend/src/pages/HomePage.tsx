import React, { useEffect, useState } from 'react';
import { Link as RouterLink } from 'react-router-dom';
import {
  Alert,
  Box,
  Button,
  Card,
  CardActionArea,
  CardContent,
  CircularProgress,
  Typography,
} from '@mui/material';
import AssignmentIcon from '@mui/icons-material/Assignment';
import AccountTreeIcon from '@mui/icons-material/AccountTree';
import RateReviewIcon from '@mui/icons-material/RateReview';
import DraftsIcon from '@mui/icons-material/Drafts';
import { PramaLayout } from '../components/PramaLayout';
import { apiClient } from '../services/apiClient';
import { authGuard } from '../services/authGuard';
import { useTranslation } from '../i18n/useTranslation';

interface Summary {
  processing: { active: number; drafts: number; review_due: number };
  tasks_overdue: number;
  tasks_due_7: number;
  assets_without_owner: number;
  agreements_to_review: number;
  documents_expiring: number;
  dpias_review_due: number;
  tia_pending: number;
  high_risks: number;
  notices_to_review?: number;
}

const HomePage: React.FC = () => {
  const user = authGuard.getCurrentUser();
  const { t } = useTranslation('privacypa');
  const [summary, setSummary] = useState<Summary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    apiClient.get<Summary>('/api/privacypa/dashboard')
      .then(setSummary)
      .catch((err: any) => setError(err.message))
      .finally(() => setLoading(false));
  }, []);

  const cards = [
    { to: '/registro', icon: <AssignmentIcon />, label: t('dashboard.active'), value: summary?.processing?.active ?? 0 },
    { to: '/registro?status=DRAFT', icon: <DraftsIcon />, label: t('dashboard.drafts'), value: summary?.processing?.drafts ?? 0 },
    { to: '/registro', icon: <RateReviewIcon />, label: t('dashboard.review_due'), value: summary?.processing?.review_due ?? 0 },
    { to: '/task', icon: <AssignmentIcon />, label: t('dashboard.tasks_overdue'), value: summary?.tasks_overdue ?? 0 },
    { to: '/sistemi', icon: <AccountTreeIcon />, label: t('dashboard.assets_no_owner'), value: summary?.assets_without_owner ?? 0 },
    { to: '/fornitori', icon: <AccountTreeIcon />, label: t('dashboard.agreements'), value: summary?.agreements_to_review ?? 0 },
    { to: '/documenti', icon: <AccountTreeIcon />, label: t('dashboard.docs_expiring'), value: summary?.documents_expiring ?? 0 },
    { to: '/dpia', icon: <AccountTreeIcon />, label: t('dashboard.dpias_due'), value: summary?.dpias_review_due ?? 0 },
    { to: '/trasferimenti', icon: <AccountTreeIcon />, label: t('dashboard.tia_pending'), value: summary?.tia_pending ?? 0 },
    { to: '/rischi', icon: <AccountTreeIcon />, label: t('dashboard.high_risks'), value: summary?.high_risks ?? 0 },
    { to: '/informative', icon: <AccountTreeIcon />, label: t('dashboard.notices_to_review'), value: summary?.notices_to_review ?? 0 },
    { to: '/organizzazione', icon: <AccountTreeIcon />, label: t('dashboard.organization'), value: null },
  ];

  return (
    <PramaLayout pageTitle={t('dashboard.title')}>
      <Box sx={{ mb: 5 }}>
        <Typography variant="h4" sx={{ mb: 0.5 }}>
          {t('dashboard.hello', { name: user?.display_name || user?.email || '' })}
        </Typography>
        <Typography variant="body1" color="text.secondary">
          {t('dashboard.subtitle')}
        </Typography>
      </Box>

      {error && <Alert severity="error" sx={{ mb: 3 }}>{error}</Alert>}
      {loading && <CircularProgress />}

      <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', sm: '1fr 1fr', md: 'repeat(4, 1fr)' }, gap: 2, mb: 4 }}>
        {cards.map((card) => (
          <Card key={card.label} variant="outlined">
            <CardActionArea component={RouterLink} to={card.to} sx={{ p: 1 }}>
              <CardContent>
                <Box sx={{ color: 'primary.main', mb: 1 }}>{card.icon}</Box>
                <Typography variant="overline" color="text.secondary">{card.label}</Typography>
                {card.value !== null && (
                  <Typography variant="h4" sx={{ fontWeight: 700 }}>{card.value}</Typography>
                )}
              </CardContent>
            </CardActionArea>
          </Card>
        ))}
      </Box>

      <Button component={RouterLink} to="/registro" variant="contained">
        {t('dashboard.open_register')}
      </Button>
    </PramaLayout>
  );
};

export default HomePage;
