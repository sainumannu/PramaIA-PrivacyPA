import React, { useEffect, useState } from 'react';
import {
  Alert, Box, Button, Chip, Container, Dialog, DialogActions, DialogContent, DialogTitle,
  FormControl, InputLabel, MenuItem, Paper, Select, Tab, Table, TableBody, TableCell, TableHead,
  TableRow, Tabs, TextField, Typography,
} from '@mui/material';
import ReportProblemIcon from '@mui/icons-material/ReportProblem';
import AddIcon from '@mui/icons-material/Add';
import { PramaLayout } from '../components/PramaLayout';
import { apiClient } from '../services/apiClient';
import { useTranslation } from '../i18n/useTranslation';

interface Risk { id: string; code: string; title: string; threat_category: string | null; description: string | null }
interface Assessment {
  id: string; code: string; risk_id: string; entity_type: string; entity_id: string;
  likelihood: number; impact: number; inherent_level: string; inherent_score: number;
  residual_level: string | null; residual_score: number | null; assessment_rationale: string;
}
interface Measure { id: string; code: string; name: string; category: string; is_global: boolean }
interface Impl { id: string; measure_id: string; entity_type: string; entity_id: string; implementation_status: string }
interface Matrix { config: { scale_min: number; scale_max: number; bands: { level: string; min_score: number; max_score: number }[] } }

const LEVEL_COLOR: Record<string, 'success' | 'warning' | 'error' | 'default'> = {
  LOW: 'success', MEDIUM: 'warning', HIGH: 'error', CRITICAL: 'error',
};

export const RisksPage: React.FC = () => {
  const { t } = useTranslation('privacypa');
  const [tab, setTab] = useState(0);
  const [risks, setRisks] = useState<Risk[]>([]);
  const [assessments, setAssessments] = useState<Assessment[]>([]);
  const [measures, setMeasures] = useState<Measure[]>([]);
  const [impls, setImpls] = useState<Impl[]>([]);
  const [matrix, setMatrix] = useState<Matrix | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [openRisk, setOpenRisk] = useState(false);
  const [openRa, setOpenRa] = useState(false);
  const [riskForm, setRiskForm] = useState({ title: '', threat_category: '', description: '' });
  const [raForm, setRaForm] = useState({
    risk_id: '', entity_type: 'dpia', entity_id: '', likelihood: 3, impact: 3,
    assessment_rationale: '', residual_likelihood: 2, residual_impact: 2, residual_rationale: '',
  });
  const [dpias, setDpias] = useState<{ id: string; code: string; title: string }[]>([]);
  const [procs, setProcs] = useState<{ id: string; code: string; title: string }[]>([]);

  const load = async () => {
    try {
      const [r, a, m, i, mx, d, p] = await Promise.all([
        apiClient.get<Risk[]>('/api/privacypa/risks'),
        apiClient.get<Assessment[]>('/api/privacypa/risk-assessments'),
        apiClient.get<Measure[]>('/api/privacypa/security-measures'),
        apiClient.get<Impl[]>('/api/privacypa/measure-implementations'),
        apiClient.get<Matrix>('/api/privacypa/risk-matrix'),
        apiClient.get<{ id: string; code: string; title: string }[]>('/api/privacypa/dpias'),
        apiClient.get<{ id: string; code: string; title: string }[]>('/api/privacypa/processing-activities'),
      ]);
      setRisks(r); setAssessments(a); setMeasures(m); setImpls(i); setMatrix(mx); setDpias(d); setProcs(p);
    } catch (e: any) { setError(e.message); }
  };
  useEffect(() => { load(); }, []);

  const riskTitle = (id: string) => risks.find((x) => x.id === id)?.title || id;
  const entityLabel = (type: string, id: string) => {
    if (type === 'dpia') return dpias.find((x) => x.id === id)?.code || id;
    if (type === 'processing_activity') return procs.find((x) => x.id === id)?.code || id;
    return id;
  };

  const saveRisk = async () => {
    if (!riskForm.title.trim()) return;
    try {
      await apiClient.post('/api/privacypa/risks', riskForm);
      setOpenRisk(false); setRiskForm({ title: '', threat_category: '', description: '' }); await load();
    } catch (e: any) { setError(e.message); }
  };

  const saveRa = async () => {
    try {
      await apiClient.post('/api/privacypa/risk-assessments', {
        ...raForm,
        residual_likelihood: raForm.residual_likelihood || null,
        residual_impact: raForm.residual_impact || null,
        residual_rationale: raForm.residual_rationale || null,
      });
      setOpenRa(false); await load();
    } catch (e: any) { setError(e.message); }
  };

  return (
    <PramaLayout pageTitle={t('risk.title')}>
      <Container maxWidth="lg">
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
          <ReportProblemIcon color="primary" />
          <Typography variant="h4" fontWeight={600}>{t('risk.title')}</Typography>
        </Box>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>{t('risk.subtitle')}</Typography>
        {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>{error}</Alert>}
        <Paper>
          <Tabs value={tab} onChange={(_, v) => setTab(v)} sx={{ borderBottom: 1, borderColor: 'divider', '& .MuiTab-root': { textTransform: 'none' } }}>
            <Tab label={t('risk.tabs.catalog')} />
            <Tab label={t('risk.tabs.assessments')} />
            <Tab label={t('risk.tabs.matrix')} />
            <Tab label={t('risk.tabs.measures')} />
          </Tabs>
          <Box sx={{ p: 2 }}>
            {tab === 0 && (
              <>
                <Button variant="contained" startIcon={<AddIcon />} sx={{ mb: 2 }} onClick={() => setOpenRisk(true)}>{t('risk.add_risk')}</Button>
                <Table size="small">
                  <TableHead><TableRow>
                    <TableCell>{t('risk.code')}</TableCell><TableCell>{t('risk.doc_title')}</TableCell>
                    <TableCell>{t('risk.category')}</TableCell>
                  </TableRow></TableHead>
                  <TableBody>
                    {risks.map((r) => (
                      <TableRow key={r.id}>
                        <TableCell sx={{ fontFamily: 'monospace' }}>{r.code}</TableCell>
                        <TableCell>{r.title}</TableCell>
                        <TableCell>{r.threat_category || '—'}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </>
            )}
            {tab === 1 && (
              <>
                <Button variant="contained" startIcon={<AddIcon />} sx={{ mb: 2 }} onClick={() => setOpenRa(true)}>{t('risk.add_assessment')}</Button>
                <Table size="small">
                  <TableHead><TableRow>
                    <TableCell>{t('risk.code')}</TableCell><TableCell>{t('risk.doc_title')}</TableCell>
                    <TableCell>{t('risk.target')}</TableCell>
                    <TableCell>{t('risk.inherent')}</TableCell>
                    <TableCell>{t('risk.residual')}</TableCell>
                  </TableRow></TableHead>
                  <TableBody>
                    {assessments.map((a) => (
                      <TableRow key={a.id}>
                        <TableCell sx={{ fontFamily: 'monospace' }}>{a.code}</TableCell>
                        <TableCell>{riskTitle(a.risk_id)}</TableCell>
                        <TableCell>{a.entity_type} {entityLabel(a.entity_type, a.entity_id)}</TableCell>
                        <TableCell><Chip size="small" color={LEVEL_COLOR[a.inherent_level]} label={`${a.inherent_level} (${a.inherent_score})`} /></TableCell>
                        <TableCell>{a.residual_level ? <Chip size="small" color={LEVEL_COLOR[a.residual_level]} label={`${a.residual_level} (${a.residual_score})`} /> : '—'}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </>
            )}
            {tab === 2 && matrix && (
              <Box>
                <Typography variant="body2" sx={{ mb: 2 }}>{t('risk.matrix_hint')}</Typography>
                <Table size="small">
                  <TableHead><TableRow>
                    <TableCell>{t('risk.level')}</TableCell>
                    <TableCell>{t('risk.score_from')}</TableCell>
                    <TableCell>{t('risk.score_to')}</TableCell>
                  </TableRow></TableHead>
                  <TableBody>
                    {matrix.config.bands.map((b) => (
                      <TableRow key={b.level}>
                        <TableCell><Chip size="small" color={LEVEL_COLOR[b.level]} label={t(`risk.lv.${b.level}`)} /></TableCell>
                        <TableCell>{b.min_score}</TableCell>
                        <TableCell>{b.max_score}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
                <Typography variant="caption" color="text.secondary" sx={{ mt: 2, display: 'block' }}>
                  {t('risk.scale')}: {matrix.config.scale_min}–{matrix.config.scale_max} · score = L × I
                </Typography>
              </Box>
            )}
            {tab === 3 && (
              <Table size="small">
                <TableHead><TableRow>
                  <TableCell>Code</TableCell><TableCell>{t('risk.measure')}</TableCell>
                  <TableCell>{t('risk.category')}</TableCell><TableCell>{t('risk.origin')}</TableCell>
                </TableRow></TableHead>
                <TableBody>
                  {measures.map((m) => (
                    <TableRow key={m.id}>
                      <TableCell sx={{ fontFamily: 'monospace' }}>{m.code}</TableCell>
                      <TableCell>{m.name}</TableCell>
                      <TableCell>{m.category}</TableCell>
                      <TableCell>{m.is_global ? t('catalogsPage.global') : t('catalogsPage.local')}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </Box>
        </Paper>
      </Container>

      <Dialog open={openRisk} onClose={() => setOpenRisk(false)} maxWidth="sm" fullWidth>
        <DialogTitle>{t('risk.add_risk')}</DialogTitle>
        <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 2 }}>
          <TextField label={t('risk.doc_title')} value={riskForm.title} onChange={(e) => setRiskForm({ ...riskForm, title: e.target.value })} />
          <TextField label={t('risk.category')} value={riskForm.threat_category} onChange={(e) => setRiskForm({ ...riskForm, threat_category: e.target.value })} />
          <TextField label={t('risk.description')} value={riskForm.description} onChange={(e) => setRiskForm({ ...riskForm, description: e.target.value })} multiline minRows={2} />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setOpenRisk(false)}>{t('common.cancel')}</Button>
          <Button variant="contained" onClick={saveRisk}>{t('common.save')}</Button>
        </DialogActions>
      </Dialog>

      <Dialog open={openRa} onClose={() => setOpenRa(false)} maxWidth="sm" fullWidth>
        <DialogTitle>{t('risk.add_assessment')}</DialogTitle>
        <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 2 }}>
          <FormControl fullWidth>
            <InputLabel>{t('risk.doc_title')}</InputLabel>
            <Select label={t('risk.doc_title')} value={raForm.risk_id} onChange={(e) => setRaForm({ ...raForm, risk_id: e.target.value })}>
              {risks.map((r) => <MenuItem key={r.id} value={r.id}>{r.code} — {r.title}</MenuItem>)}
            </Select>
          </FormControl>
          <FormControl fullWidth>
            <InputLabel>{t('risk.target_type')}</InputLabel>
            <Select label={t('risk.target_type')} value={raForm.entity_type} onChange={(e) => setRaForm({ ...raForm, entity_type: e.target.value, entity_id: '' })}>
              <MenuItem value="dpia">DPIA</MenuItem>
              <MenuItem value="processing_activity">{t('risk.processing')}</MenuItem>
            </Select>
          </FormControl>
          <FormControl fullWidth>
            <InputLabel>{t('risk.target')}</InputLabel>
            <Select label={t('risk.target')} value={raForm.entity_id} onChange={(e) => setRaForm({ ...raForm, entity_id: e.target.value })}>
              {(raForm.entity_type === 'dpia' ? dpias : procs).map((x) => (
                <MenuItem key={x.id} value={x.id}>{x.code} — {x.title}</MenuItem>
              ))}
            </Select>
          </FormControl>
          <TextField type="number" label={t('risk.likelihood')} value={raForm.likelihood} onChange={(e) => setRaForm({ ...raForm, likelihood: Number(e.target.value) })} inputProps={{ min: 1, max: 5 }} />
          <TextField type="number" label={t('risk.impact')} value={raForm.impact} onChange={(e) => setRaForm({ ...raForm, impact: Number(e.target.value) })} inputProps={{ min: 1, max: 5 }} />
          <TextField label={t('risk.rationale')} value={raForm.assessment_rationale} onChange={(e) => setRaForm({ ...raForm, assessment_rationale: e.target.value })} multiline minRows={2} required />
          <TextField type="number" label={t('risk.res_likelihood')} value={raForm.residual_likelihood} onChange={(e) => setRaForm({ ...raForm, residual_likelihood: Number(e.target.value) })} inputProps={{ min: 1, max: 5 }} />
          <TextField type="number" label={t('risk.res_impact')} value={raForm.residual_impact} onChange={(e) => setRaForm({ ...raForm, residual_impact: Number(e.target.value) })} inputProps={{ min: 1, max: 5 }} />
          <TextField label={t('risk.res_rationale')} value={raForm.residual_rationale} onChange={(e) => setRaForm({ ...raForm, residual_rationale: e.target.value })} multiline minRows={2} />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setOpenRa(false)}>{t('common.cancel')}</Button>
          <Button variant="contained" onClick={saveRa}>{t('common.save')}</Button>
        </DialogActions>
      </Dialog>
    </PramaLayout>
  );
};
export default RisksPage;
