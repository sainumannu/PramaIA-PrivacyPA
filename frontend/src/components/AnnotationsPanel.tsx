import React, { useEffect, useState } from 'react';
import {
  Alert, Autocomplete, Box, Button, Checkbox, Chip, Dialog, DialogActions, DialogContent, DialogTitle, FormControl, FormControlLabel,
  IconButton, InputLabel, MenuItem, Select, TextField, Tooltip, Typography,
} from '@mui/material';
import ChatBubbleOutlineIcon from '@mui/icons-material/ChatBubbleOutline';
import { apiClient } from '../services/apiClient';
import { usePermissions } from '../services/permissions';
import { useTranslation } from '../i18n/useTranslation';
import { ErrorBanner } from './ErrorBanner';

const VISIBILITIES = ['TEAM', 'DPO_ONLY', 'PRIVATE', 'ALL_AUTHORIZED'];
const stamp = (iso: string | null) => (iso ? iso.replace('T', ' ').slice(0, 16) : '');

// Tag, campi custom e commenti di un elemento (§77-§79). L'elemento è identificato da tipo e id; il server decide cosa si vede e cosa si scrive.
export const AnnotationsPanel: React.FC<{ entityType: string; entityId: string }> = ({ entityType, entityId }) => {
  const { t } = useTranslation('privacypa');
  const { can } = usePermissions();
  const base = `/api/privacypa/entities/${entityType}/${entityId}`;
  const [data, setData] = useState<any | null>(null);
  const [catalog, setCatalog] = useState<any[]>([]);
  const [values, setValues] = useState<Record<string, any>>({});
  const [body, setBody] = useState('');
  const [visibility, setVisibility] = useState('TEAM');
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);

  const load = async () => {
    try {
      const fresh = await apiClient.get<any>(`${base}/annotations`);
      setData(fresh);
      setValues(Object.fromEntries(fresh.custom_fields.map((f: any) => [f.key, f.value ?? ''])));
    } catch (err: any) {
      setError(err.message);
    }
  };
  useEffect(() => {
    load();
    apiClient.get<any[]>('/api/privacypa/tags?active=true').then(setCatalog).catch(() => undefined);
  }, [entityType, entityId]);

  const fail = (err: any) => { setInfo(null); setError(err.message); };
  const saveTags = (tags: any[]) =>
    apiClient.put<any[]>(`${base}/tags`, { tag_ids: tags.map((x) => x.id) }).then((next) => { setData({ ...data, tags: next }); setError(null); }).catch(fail);
  const saveValues = () => {
    const payload: Record<string, any> = {};
    data.custom_fields.filter((f: any) => f.active).forEach((f: any) => {
      const v = values[f.key];
      payload[f.key] = v === '' || v === undefined ? null : f.field_type === 'NUMBER' ? Number(v) : v;
    });
    apiClient.put<any[]>(`${base}/custom-fields`, { values: payload })
      .then(() => { setError(null); setInfo(t('annotations.saved')); return load(); }).catch(fail);
  };
  const addComment = () =>
    apiClient.post(`${base}/comments`, { body, visibility }).then(() => { setBody(''); setError(null); return load(); }).catch(fail);
  const removeComment = (id: string) =>
    apiClient.delete(`/api/privacypa/comments/${id}`).then(load).catch(fail);

  if (!data) return error ? <Alert severity="error">{error}</Alert> : null;

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
      <ErrorBanner message={error} onClose={() => setError(null)} />
      {info && <Alert severity="success" onClose={() => setInfo(null)}>{info}</Alert>}

      <Box>
        <Typography variant="subtitle2" sx={{ mb: 0.5 }}>{t('annotations.tags')}</Typography>
        {can('tag.assign') ? (
          <Autocomplete
            multiple size="small" options={catalog} value={data.tags} getOptionLabel={(o: any) => o.name}
            isOptionEqualToValue={(a: any, b: any) => a.id === b.id} onChange={(_, next) => saveTags(next)}
            renderInput={(params) => <TextField {...params} placeholder={t('annotations.tags_placeholder')} />}
          />
        ) : (
          <Box sx={{ display: 'flex', gap: 0.5, flexWrap: 'wrap' }}>
            {data.tags.length === 0 && <Typography variant="body2" color="text.secondary">—</Typography>}
            {data.tags.map((tag: any) => <Chip key={tag.id} size="small" label={tag.name} />)}
          </Box>
        )}
      </Box>

      {data.custom_fields_supported && data.custom_fields.length > 0 && (
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1.5 }}>
          <Typography variant="subtitle2">{t('annotations.custom_fields')}</Typography>
          {data.custom_fields.map((f: any) => {
            const common = { key: f.key, label: f.label, required: f.required, disabled: !f.active, size: 'small' as const };
            if (f.field_type === 'BOOLEAN') {
              return <FormControlLabel key={f.key} disabled={!f.active} label={f.label}
                control={<Checkbox checked={values[f.key] === true} onChange={(e) => setValues({ ...values, [f.key]: e.target.checked })} />} />;
            }
            if (f.field_type === 'SELECT') {
              return (
                <FormControl key={f.key} size="small" disabled={!f.active} required={f.required}>
                  <InputLabel>{f.label}</InputLabel>
                  <Select label={f.label} value={values[f.key] ?? ''} onChange={(e) => setValues({ ...values, [f.key]: e.target.value })}>
                    <MenuItem value="">—</MenuItem>
                    {f.options.map((o: string) => <MenuItem key={o} value={o}>{o}</MenuItem>)}
                  </Select>
                </FormControl>
              );
            }
            return (
              <TextField {...common} value={values[f.key] ?? ''} type={f.field_type === 'NUMBER' ? 'number' : f.field_type === 'DATE' ? 'date' : 'text'}
                InputLabelProps={f.field_type === 'DATE' ? { shrink: true } : undefined}
                onChange={(e) => setValues({ ...values, [f.key]: e.target.value })} />
            );
          })}
          <Box><Button variant="outlined" size="small" onClick={saveValues}>{t('annotations.save_fields')}</Button></Box>
        </Box>
      )}

      <Box>
        <Typography variant="subtitle2" sx={{ mb: 0.5 }}>{t('annotations.comments')}</Typography>
        <Typography variant="caption" color="text.secondary" display="block" sx={{ mb: 1 }}>{t('annotations.comments_hint')}</Typography>
        {data.comments.length === 0 && <Typography variant="body2" color="text.secondary">{t('annotations.no_comments')}</Typography>}
        {data.comments.map((c: any) => (
          <Box key={c.id} sx={{ py: 1, borderBottom: 1, borderColor: 'divider' }}>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
              <Typography variant="caption" fontWeight={600}>{c.author_id}</Typography>
              <Typography variant="caption" color="text.secondary">{stamp(c.created_at)}{c.edited_at ? ` · ${t('annotations.edited')}` : ''}</Typography>
              <Chip size="small" variant="outlined" label={t(`annotations.visibility.${c.visibility}`)} />
              <Box sx={{ flex: 1 }} />
              {c.can_delete && <Button size="small" color="error" onClick={() => removeComment(c.id)}>{t('wf.delete')}</Button>}
            </Box>
            <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap' }}>{c.body}</Typography>
          </Box>
        ))}
        {can('comment.create') && (
          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1, mt: 1.5 }}>
            <TextField multiline minRows={2} size="small" label={t('annotations.new_comment')} value={body} inputProps={{ maxLength: 4000 }} onChange={(e) => setBody(e.target.value)} />
            <Box sx={{ display: 'flex', gap: 1 }}>
              <FormControl size="small" sx={{ minWidth: 220 }}>
                <InputLabel>{t('annotations.visible_to')}</InputLabel>
                <Select label={t('annotations.visible_to')} value={visibility} onChange={(e) => setVisibility(e.target.value)}>
                  {VISIBILITIES.map((v) => <MenuItem key={v} value={v}>{t(`annotations.visibility.${v}`)}</MenuItem>)}
                </Select>
              </FormControl>
              <Button variant="contained" size="small" disabled={!body.trim()} onClick={addComment}>{t('annotations.add_comment')}</Button>
            </Box>
          </Box>
        )}
      </Box>
    </Box>
  );
};

// Pulsante compatto da inserire in una riga: apre le annotazioni dell'elemento in una finestra.
export const AnnotationsButton: React.FC<{ entityType: string; entityId: string; title?: string }> = ({ entityType, entityId, title }) => {
  const { t } = useTranslation('privacypa');
  const [open, setOpen] = useState(false);
  return (
    <>
      <Tooltip title={t('annotations.open')}>
        <IconButton size="small" onClick={(e) => { e.stopPropagation(); setOpen(true); }}><ChatBubbleOutlineIcon fontSize="small" /></IconButton>
      </Tooltip>
      {open && (
        <Dialog open onClose={() => setOpen(false)} maxWidth="sm" fullWidth onClick={(e) => e.stopPropagation()}>
          <DialogTitle>{t('annotations.title')}{title ? ` — ${title}` : ''}</DialogTitle>
          <DialogContent sx={{ pt: 2 }}><AnnotationsPanel entityType={entityType} entityId={entityId} /></DialogContent>
          <DialogActions><Button onClick={() => setOpen(false)}>{t('wf.close')}</Button></DialogActions>
        </Dialog>
      )}
    </>
  );
};
