import React, { useEffect, useState } from 'react';
import {
  Alert, Box, Button, Chip, Container, Dialog, DialogActions, DialogContent, DialogTitle, FormControl,
  IconButton, InputLabel, MenuItem, Paper, Select, Tab, Table, TableBody, TableCell, TableHead, TableRow,
  Tabs, TextField, Tooltip, Typography,
} from '@mui/material';
import AccountTreeIcon from '@mui/icons-material/AccountTree';
import AddIcon from '@mui/icons-material/Add';
import DeleteIcon from '@mui/icons-material/Delete';
import EditIcon from '@mui/icons-material/Edit';
import LinkIcon from '@mui/icons-material/Link';
import { PramaLayout } from '../components/PramaLayout';
import { apiClient } from '../services/apiClient';
import { usePermissions } from '../services/permissions';
import { useTranslation } from '../i18n/useTranslation';
import { ErrorBanner } from '../components/ErrorBanner';
import { PartyRef, PartySelect } from '../components/PartyPicker';

interface Contract {
  id: string; row_version: number; code: string; party_id: string; title: string; cig: string | null; cup: string | null;
  start_date: string | null; end_date: string | null; renewal_date: string | null; status: string;
  organizational_unit_id: string | null; document_id: string | null;
}
interface Subprocessor {
  id: string; row_version: number; processor_party_id: string; subprocessor_party_id: string; service_description: string;
  authorization_type: string; authorization_date: string | null; valid_from: string | null; valid_to: string | null;
  countries: string[]; status: string;
}
interface TreeNode { party_id: string; party_name: string | null; children: (Subprocessor & { party_name: string | null; children: TreeNode['children'] })[] }

const CONTRACT_STATUSES = ['DRAFT', 'ACTIVE', 'EXPIRED', 'TERMINATED'];
const SUB_STATUSES = ['ACTIVE', 'TO_REVIEW', 'SUSPENDED', 'ENDED'];
const emptyContract = { party_id: '', title: '', cig: '', cup: '', start_date: '', end_date: '', renewal_date: '', status: 'DRAFT' };
const emptySub = {
  processor_party_id: '', subprocessor_party_id: '', service_description: '', authorization_type: 'SPECIFIC',
  authorization_date: '', valid_from: '', valid_to: '', countries: '', status: 'TO_REVIEW',
};

const renderTree = (nodes: TreeNode['children'], depth = 0): React.ReactNode =>
  nodes.map((n) => (
    <Box key={n.id} sx={{ pl: depth * 3, py: 0.5 }}>
      <Typography variant="body2">
        {'└ '}<strong>{n.party_name}</strong>{' — '}{n.service_description}
        {n.countries.length > 0 && ` (${n.countries.join(', ')})`}
      </Typography>
      {renderTree(n.children, depth + 1)}
    </Box>
  ));

export const SupplyChainPage: React.FC = () => {
  const { t } = useTranslation('privacypa');
  const { can } = usePermissions();
  const [tab, setTab] = useState(0);
  const [parties, setParties] = useState<PartyRef[]>([]);
  const [contracts, setContracts] = useState<Contract[]>([]);
  const [subs, setSubs] = useState<Subprocessor[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [dialogError, setDialogError] = useState<string | null>(null);
  const [contractDialog, setContractDialog] = useState<{ open: boolean; editing: Contract | null }>({ open: false, editing: null });
  const [subDialog, setSubDialog] = useState<{ open: boolean; editing: Subprocessor | null }>({ open: false, editing: null });
  const [cForm, setCForm] = useState(emptyContract);
  const [sForm, setSForm] = useState(emptySub);
  const [tree, setTree] = useState<TreeNode | null>(null);

  const load = async () => {
    try {
      const [p, c, s] = await Promise.all([
        apiClient.get<PartyRef[]>('/api/privacypa/parties'),
        apiClient.get<Contract[]>('/api/privacypa/contracts'),
        apiClient.get<Subprocessor[]>('/api/privacypa/subprocessors'),
      ]);
      setParties(p); setContracts(c); setSubs(s);
    } catch (err: any) {
      setError(err.message);
    }
  };
  useEffect(() => { load(); }, []);

  const name = (id: string) => parties.find((p) => p.id === id)?.legal_name || id;
  const canWrite = can('party.edit');

  const openContract = (row: Contract | null) => {
    setDialogError(null);
    setCForm(row ? {
      party_id: row.party_id, title: row.title, cig: row.cig || '', cup: row.cup || '', start_date: row.start_date || '',
      end_date: row.end_date || '', renewal_date: row.renewal_date || '', status: row.status,
    } : emptyContract);
    setContractDialog({ open: true, editing: row });
  };

  const saveContract = async () => {
    if (!cForm.party_id || !cForm.title.trim()) { setDialogError(t('common.fill_required')); return; }
    const body = {
      party_id: cForm.party_id, title: cForm.title.trim(), cig: cForm.cig.trim() || null, cup: cForm.cup.trim() || null,
      start_date: cForm.start_date || null, end_date: cForm.end_date || null, renewal_date: cForm.renewal_date || null,
      status: cForm.status,
      // Il PATCH sostituisce il record: unità e documento (non nel modulo) si rimandano com'erano.
      organizational_unit_id: contractDialog.editing?.organizational_unit_id ?? null,
      document_id: contractDialog.editing?.document_id ?? null,
    };
    try {
      const { editing } = contractDialog;
      if (editing) await apiClient.patch(`/api/privacypa/contracts/${editing.id}`, body, { version: editing.row_version });
      else await apiClient.post('/api/privacypa/contracts', body);
      setContractDialog({ open: false, editing: null });
      await load();
    } catch (err: any) {
      setDialogError(err.message);
    }
  };

  const removeContract = async (row: Contract) => {
    if (!window.confirm(t('supply.confirm_delete'))) return;
    try {
      await apiClient.delete(`/api/privacypa/contracts/${row.id}`);
      await load();
    } catch (err: any) {
      setError(err.message);
    }
  };

  const openSub = (row: Subprocessor | null) => {
    setDialogError(null);
    setSForm(row ? {
      processor_party_id: row.processor_party_id, subprocessor_party_id: row.subprocessor_party_id,
      service_description: row.service_description, authorization_type: row.authorization_type,
      authorization_date: row.authorization_date || '', valid_from: row.valid_from || '', valid_to: row.valid_to || '',
      countries: row.countries.join(', '), status: row.status,
    } : emptySub);
    setSubDialog({ open: true, editing: row });
  };

  const saveSub = async () => {
    if (!sForm.processor_party_id || !sForm.subprocessor_party_id || !sForm.service_description.trim()) {
      setDialogError(t('common.fill_required')); return;
    }
    const body = {
      processor_party_id: sForm.processor_party_id, subprocessor_party_id: sForm.subprocessor_party_id,
      service_description: sForm.service_description.trim(), authorization_type: sForm.authorization_type,
      authorization_date: sForm.authorization_date || null, valid_from: sForm.valid_from || null,
      valid_to: sForm.valid_to || null, status: sForm.status,
      countries: sForm.countries.split(',').map((c) => c.trim()).filter(Boolean),
    };
    try {
      const { editing } = subDialog;
      if (editing) await apiClient.patch(`/api/privacypa/subprocessors/${editing.id}`, body, { version: editing.row_version });
      else await apiClient.post('/api/privacypa/subprocessors', body);
      setSubDialog({ open: false, editing: null });
      await load();
    } catch (err: any) {
      setDialogError(err.message);
    }
  };

  const removeSub = async (row: Subprocessor) => {
    if (!window.confirm(t('supply.confirm_delete'))) return;
    try {
      await apiClient.delete(`/api/privacypa/subprocessors/${row.id}`);
      await load();
    } catch (err: any) {
      setError(err.message);
    }
  };

  // Fornitori e responsabili si scelgono dall'anagrafica; chi manca si registra dal campo stesso.
  const partySelect = (label: string, value: string, onChange: (v: string) => void, disabled: boolean, defaultType: string) => (
    <PartySelect label={label} value={value} onChange={onChange} disabled={disabled} required defaultType={defaultType}
      parties={parties} onPartyAdded={(p) => { setParties((list) => [...list, p]); load(); }} />
  );

  return (
    <PramaLayout pageTitle={t('supply.title')}>
      <Container maxWidth="lg">
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
          <LinkIcon color="primary" />
          <Typography variant="h4" fontWeight={600}>{t('supply.title')}</Typography>
          <Box sx={{ flex: 1 }} />
          {can('party.create') && (
            <Button variant="contained" startIcon={<AddIcon />} onClick={() => (tab === 0 ? openContract(null) : openSub(null))}>
              {tab === 0 ? t('supply.add_contract') : t('supply.add_subprocessor')}
            </Button>
          )}
        </Box>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>{t('supply.subtitle')}</Typography>
        <ErrorBanner message={error} onClose={() => setError(null)} />
        <Tabs value={tab} onChange={(_, v) => setTab(v)} sx={{ mb: 2 }}>
          <Tab label={t('supply.tabs.contracts')} />
          <Tab label={t('supply.tabs.subprocessors')} />
        </Tabs>

        {tab === 0 && (
          <Paper>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>{t('supply.code')}</TableCell><TableCell>{t('supply.contract_title')}</TableCell>
                  <TableCell>{t('supply.party')}</TableCell><TableCell>{t('supply.validity')}</TableCell>
                  <TableCell>{t('supply.status')}</TableCell><TableCell />
                </TableRow>
              </TableHead>
              <TableBody>
                {contracts.length === 0 && <TableRow><TableCell colSpan={6}><Typography variant="body2" color="text.secondary">{t('supply.empty')}</Typography></TableCell></TableRow>}
                {contracts.map((c) => (
                  <TableRow key={c.id} hover>
                    <TableCell sx={{ fontFamily: 'monospace' }}>{c.code}</TableCell>
                    <TableCell>{c.title}{c.cig && <Typography variant="caption" display="block">CIG {c.cig}</Typography>}</TableCell>
                    <TableCell>{name(c.party_id)}</TableCell>
                    <TableCell>{c.start_date || '—'} → {c.end_date || '—'}{c.renewal_date && <Typography variant="caption" display="block">{t('supply.renewal')}: {c.renewal_date}</Typography>}</TableCell>
                    <TableCell><Chip size="small" label={t(`supply.contract_status.${c.status}`)} /></TableCell>
                    <TableCell align="right">
                      {canWrite && <Tooltip title={t('common.edit')}><IconButton size="small" onClick={() => openContract(c)}><EditIcon fontSize="small" /></IconButton></Tooltip>}
                      {can('party.delete') && <Tooltip title={t('supply.delete')}><IconButton size="small" onClick={() => removeContract(c)}><DeleteIcon fontSize="small" /></IconButton></Tooltip>}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </Paper>
        )}

        {tab === 1 && (
          <Paper>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>{t('supply.processor')}</TableCell><TableCell>{t('supply.subprocessor')}</TableCell>
                  <TableCell>{t('supply.service')}</TableCell><TableCell>{t('supply.authorization')}</TableCell>
                  <TableCell>{t('supply.status')}</TableCell><TableCell />
                </TableRow>
              </TableHead>
              <TableBody>
                {subs.length === 0 && <TableRow><TableCell colSpan={6}><Typography variant="body2" color="text.secondary">{t('supply.empty')}</Typography></TableCell></TableRow>}
                {subs.map((s) => (
                  <TableRow key={s.id} hover>
                    <TableCell>{name(s.processor_party_id)}</TableCell>
                    <TableCell>{name(s.subprocessor_party_id)}{s.countries.length > 0 && <Typography variant="caption" display="block">{s.countries.join(', ')}</Typography>}</TableCell>
                    <TableCell>{s.service_description}</TableCell>
                    <TableCell>
                      {t(`supply.auth.${s.authorization_type}`)}
                      <Typography variant="caption" display="block">{s.authorization_date || '—'}{s.valid_to ? ` → ${s.valid_to}` : ''}</Typography>
                    </TableCell>
                    <TableCell><Chip size="small" label={t(`supply.sub_status.${s.status}`)} /></TableCell>
                    <TableCell align="right">
                      <Tooltip title={t('supply.tree')}>
                        <IconButton size="small" onClick={async () => setTree(await apiClient.get<TreeNode>(`/api/privacypa/subprocessors/tree/${s.processor_party_id}`))}>
                          <AccountTreeIcon fontSize="small" />
                        </IconButton>
                      </Tooltip>
                      {canWrite && <Tooltip title={t('common.edit')}><IconButton size="small" onClick={() => openSub(s)}><EditIcon fontSize="small" /></IconButton></Tooltip>}
                      {can('party.delete') && <Tooltip title={t('supply.delete')}><IconButton size="small" onClick={() => removeSub(s)}><DeleteIcon fontSize="small" /></IconButton></Tooltip>}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </Paper>
        )}
      </Container>

      <Dialog open={contractDialog.open} onClose={() => setContractDialog({ open: false, editing: null })} maxWidth="sm" fullWidth>
        <DialogTitle>{contractDialog.editing ? t('supply.edit_contract') : t('supply.add_contract')}</DialogTitle>
        <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 2 }}>
          {dialogError && <Alert severity="error" onClose={() => setDialogError(null)}>{dialogError}</Alert>}
          {partySelect(t('supply.party'), cForm.party_id, (v) => setCForm((f) => ({ ...f, party_id: v })), Boolean(contractDialog.editing), 'SUPPLIER')}
          <TextField label={t('supply.contract_title')} value={cForm.title} required onChange={(e) => setCForm({ ...cForm, title: e.target.value })} />
          <Box sx={{ display: 'flex', gap: 2 }}>
            <TextField label="CIG" value={cForm.cig} onChange={(e) => setCForm({ ...cForm, cig: e.target.value })} inputProps={{ maxLength: 10 }} />
            <TextField label="CUP" value={cForm.cup} onChange={(e) => setCForm({ ...cForm, cup: e.target.value })} inputProps={{ maxLength: 15 }} />
          </Box>
          <Box sx={{ display: 'flex', gap: 2 }}>
            <TextField type="date" label={t('supply.start')} InputLabelProps={{ shrink: true }} value={cForm.start_date} onChange={(e) => setCForm({ ...cForm, start_date: e.target.value })} />
            <TextField type="date" label={t('supply.end')} InputLabelProps={{ shrink: true }} value={cForm.end_date} onChange={(e) => setCForm({ ...cForm, end_date: e.target.value })} />
            <TextField type="date" label={t('supply.renewal')} InputLabelProps={{ shrink: true }} value={cForm.renewal_date} onChange={(e) => setCForm({ ...cForm, renewal_date: e.target.value })} />
          </Box>
          <FormControl fullWidth>
            <InputLabel>{t('supply.status')}</InputLabel>
            <Select label={t('supply.status')} value={cForm.status} onChange={(e) => setCForm({ ...cForm, status: e.target.value })}>
              {CONTRACT_STATUSES.map((s) => <MenuItem key={s} value={s}>{t(`supply.contract_status.${s}`)}</MenuItem>)}
            </Select>
          </FormControl>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setContractDialog({ open: false, editing: null })}>{t('common.cancel')}</Button>
          <Button variant="contained" onClick={saveContract}>{t('common.save')}</Button>
        </DialogActions>
      </Dialog>

      <Dialog open={subDialog.open} onClose={() => setSubDialog({ open: false, editing: null })} maxWidth="sm" fullWidth>
        <DialogTitle>{subDialog.editing ? t('supply.edit_subprocessor') : t('supply.add_subprocessor')}</DialogTitle>
        <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 2 }}>
          {dialogError && <Alert severity="error" onClose={() => setDialogError(null)}>{dialogError}</Alert>}
          {partySelect(t('supply.processor'), sForm.processor_party_id, (v) => setSForm((f) => ({ ...f, processor_party_id: v })), Boolean(subDialog.editing), 'PROCESSOR')}
          {partySelect(t('supply.subprocessor'), sForm.subprocessor_party_id, (v) => setSForm((f) => ({ ...f, subprocessor_party_id: v })), Boolean(subDialog.editing), 'PROCESSOR')}
          <TextField label={t('supply.service')} value={sForm.service_description} required multiline minRows={2} onChange={(e) => setSForm({ ...sForm, service_description: e.target.value })} />
          <Box sx={{ display: 'flex', gap: 2 }}>
            <FormControl fullWidth>
              <InputLabel>{t('supply.authorization')}</InputLabel>
              <Select label={t('supply.authorization')} value={sForm.authorization_type} onChange={(e) => setSForm({ ...sForm, authorization_type: e.target.value })}>
                {['SPECIFIC', 'GENERAL'].map((a) => <MenuItem key={a} value={a}>{t(`supply.auth.${a}`)}</MenuItem>)}
              </Select>
            </FormControl>
            <FormControl fullWidth>
              <InputLabel>{t('supply.status')}</InputLabel>
              <Select label={t('supply.status')} value={sForm.status} onChange={(e) => setSForm({ ...sForm, status: e.target.value })}>
                {SUB_STATUSES.map((s) => <MenuItem key={s} value={s}>{t(`supply.sub_status.${s}`)}</MenuItem>)}
              </Select>
            </FormControl>
          </Box>
          <Box sx={{ display: 'flex', gap: 2 }}>
            <TextField type="date" label={t('supply.authorization_date')} InputLabelProps={{ shrink: true }} value={sForm.authorization_date} onChange={(e) => setSForm({ ...sForm, authorization_date: e.target.value })} />
            <TextField type="date" label={t('supply.valid_from')} InputLabelProps={{ shrink: true }} value={sForm.valid_from} onChange={(e) => setSForm({ ...sForm, valid_from: e.target.value })} />
            <TextField type="date" label={t('supply.valid_to')} InputLabelProps={{ shrink: true }} value={sForm.valid_to} onChange={(e) => setSForm({ ...sForm, valid_to: e.target.value })} />
          </Box>
          <TextField label={t('supply.countries')} helperText={t('supply.countries_hint')} value={sForm.countries} onChange={(e) => setSForm({ ...sForm, countries: e.target.value })} />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setSubDialog({ open: false, editing: null })}>{t('common.cancel')}</Button>
          <Button variant="contained" onClick={saveSub}>{t('common.save')}</Button>
        </DialogActions>
      </Dialog>

      <Dialog open={Boolean(tree)} onClose={() => setTree(null)} maxWidth="sm" fullWidth>
        <DialogTitle>{t('supply.tree_of', { name: tree?.party_name || '' })}</DialogTitle>
        <DialogContent>
          {tree && tree.children.length === 0 && <Typography variant="body2" color="text.secondary">{t('supply.no_children')}</Typography>}
          {tree && renderTree(tree.children)}
        </DialogContent>
        <DialogActions><Button onClick={() => setTree(null)}>{t('supply.close')}</Button></DialogActions>
      </Dialog>
    </PramaLayout>
  );
};

export default SupplyChainPage;
