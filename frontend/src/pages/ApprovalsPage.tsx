import React, { useEffect, useState } from 'react';
import {
  Alert, Box, Button, Chip, Container, Paper, Tab, Table, TableBody, TableCell, TableContainer,
  TableHead, TableRow, Tabs, Tooltip, Typography,
} from '@mui/material';
import FactCheckIcon from '@mui/icons-material/FactCheck';
import { Link as RouterLink } from 'react-router-dom';
import { PramaLayout } from '../components/PramaLayout';
import StatusNotesDialog from '../components/StatusNotesDialog';
import { apiClient } from '../services/apiClient';
import { useTranslation } from '../i18n/useTranslation';

interface Approval {
  id: string;
  entity_type: 'processing_activity' | 'dpia' | 'privacy_notice';
  entity_id: string;
  entity_code: string | null;
  entity_title: string | null;
  state: 'PENDING' | 'DECIDED' | 'CANCELLED';
  requested_by: string;
  requested_at: string;
  request_notes: string | null;
  approver_id: string | null;
  decision: 'APPROVED' | 'REJECTED' | 'CHANGES_REQUESTED' | null;
  decision_at: string | null;
  notes: string | null;
  stale: boolean;
  can_decide: boolean;
  blocked_reason: string | null;
}

type Decision = 'APPROVED' | 'REJECTED' | 'CHANGES_REQUESTED';

const TAB_QUERY = ['?decidable=true', '?state=PENDING', ''];
const ENTITY_ROUTE: Record<Approval['entity_type'], string> = {
  processing_activity: '/registro',
  dpia: '/dpia',
  privacy_notice: '/informative',
};
const DECISION_COLOR: Record<Decision, 'success' | 'error' | 'warning'> = {
  APPROVED: 'success',
  REJECTED: 'error',
  CHANGES_REQUESTED: 'warning',
};

const formatDate = (iso: string | null) => (iso ? new Date(iso).toLocaleString() : '—');

export const ApprovalsPage: React.FC = () => {
  const { t } = useTranslation('privacypa');
  const [tab, setTab] = useState(0);
  const [items, setItems] = useState<Approval[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [deciding, setDeciding] = useState<{ approval: Approval; decision: Decision } | null>(null);
  const [dialogError, setDialogError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = async (index = tab) => {
    try {
      setItems(await apiClient.get<Approval[]>(`/api/privacypa/approvals${TAB_QUERY[index]}`));
    } catch (err: any) {
      setError(err.message || t('approvals.load_failed'));
    }
  };

  useEffect(() => { load(tab); }, [tab]);

  const confirm = async (notes: string) => {
    if (!deciding) return;
    setBusy(true);
    setDialogError(null);
    try {
      await apiClient.post(`/api/privacypa/approvals/${deciding.approval.id}/decision`, {
        decision: deciding.decision,
        notes: notes || null,
      });
      setDeciding(null);
      setSuccess(t('approvals.decided'));
      await load();
    } catch (err: any) {
      setDialogError(err.message);
    } finally {
      setBusy(false);
    }
  };

  const stateChip = (a: Approval) => {
    if (a.decision) {
      return <Chip size="small" color={DECISION_COLOR[a.decision]} label={t(`approvals.decision.${a.decision}`)} />;
    }
    if (a.state === 'CANCELLED') return <Chip size="small" label={t('approvals.state.CANCELLED')} />;
    return (
      <>
        <Chip size="small" color="warning" label={t('approvals.state.PENDING')} />
        {a.stale && <Chip size="small" color="error" variant="outlined" sx={{ ml: 0.5 }} label={t('approvals.stale')} />}
      </>
    );
  };

  return (
    <PramaLayout pageTitle={t('approvals.title')}>
      <Container maxWidth="lg">
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
          <FactCheckIcon color="primary" />
          <Typography variant="h4" fontWeight={600}>{t('approvals.title')}</Typography>
        </Box>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>{t('approvals.subtitle')}</Typography>
        {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>{error}</Alert>}
        {success && <Alert severity="success" sx={{ mb: 2 }} onClose={() => setSuccess(null)}>{success}</Alert>}

        <Tabs value={tab} onChange={(_, v) => setTab(v)} sx={{ mb: 2 }}>
          <Tab label={t('approvals.tabs.to_decide')} />
          <Tab label={t('approvals.tabs.pending')} />
          <Tab label={t('approvals.tabs.history')} />
        </Tabs>

        <Paper>
          <TableContainer>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>{t('approvals.entity')}</TableCell>
                  <TableCell>{t('approvals.requested')}</TableCell>
                  <TableCell>{t('approvals.status')}</TableCell>
                  <TableCell>{t('approvals.decided_by')}</TableCell>
                  <TableCell />
                </TableRow>
              </TableHead>
              <TableBody>
                {items.length === 0 && (
                  <TableRow>
                    <TableCell colSpan={5}>
                      <Typography variant="body2" color="text.secondary">{t('approvals.empty')}</Typography>
                    </TableCell>
                  </TableRow>
                )}
                {items.map((a) => (
                  <TableRow key={a.id} hover>
                    <TableCell>
                      <Chip size="small" variant="outlined" label={t(`approvals.type.${a.entity_type}`)} sx={{ mr: 1 }} />
                      <Button
                        size="small"
                        component={RouterLink}
                        to={ENTITY_ROUTE[a.entity_type]}
                        sx={{ textTransform: 'none' }}
                      >
                        {[a.entity_code, a.entity_title].filter(Boolean).join(' — ')}
                      </Button>
                    </TableCell>
                    <TableCell>
                      <Typography variant="body2">{a.requested_by}</Typography>
                      <Typography variant="caption" color="text.secondary">{formatDate(a.requested_at)}</Typography>
                      {a.request_notes && <Typography variant="caption" display="block">{a.request_notes}</Typography>}
                    </TableCell>
                    <TableCell>{stateChip(a)}</TableCell>
                    <TableCell>
                      {a.approver_id ? (
                        <>
                          <Typography variant="body2">{a.approver_id}</Typography>
                          <Typography variant="caption" color="text.secondary">{formatDate(a.decision_at)}</Typography>
                          {a.notes && <Typography variant="caption" display="block">{a.notes}</Typography>}
                        </>
                      ) : '—'}
                    </TableCell>
                    <TableCell align="right">
                      {a.state === 'PENDING' && a.can_decide && (
                        <>
                          <Button size="small" color="success" onClick={() => setDeciding({ approval: a, decision: 'APPROVED' })}>
                            {t('approvals.actions.approve')}
                          </Button>
                          <Button size="small" color="warning" onClick={() => setDeciding({ approval: a, decision: 'CHANGES_REQUESTED' })}>
                            {t('approvals.actions.changes')}
                          </Button>
                          <Button size="small" color="error" onClick={() => setDeciding({ approval: a, decision: 'REJECTED' })}>
                            {t('approvals.actions.reject')}
                          </Button>
                        </>
                      )}
                      {a.state === 'PENDING' && !a.can_decide && a.blocked_reason && (
                        <Tooltip title={a.blocked_reason}>
                          <Chip size="small" variant="outlined" label={t('approvals.blocked')} />
                        </Tooltip>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
        </Paper>
      </Container>

      <StatusNotesDialog
        open={Boolean(deciding)}
        title={deciding ? t(`approvals.dialog.${deciding.decision}`) : ''}
        hint={deciding && deciding.decision !== 'APPROVED' ? t('approvals.dialog.note_required') : undefined}
        required={Boolean(deciding && deciding.decision !== 'APPROVED')}
        error={dialogError}
        busy={busy}
        onConfirm={confirm}
        onClose={() => { setDeciding(null); setDialogError(null); }}
      />
    </PramaLayout>
  );
};

export default ApprovalsPage;
