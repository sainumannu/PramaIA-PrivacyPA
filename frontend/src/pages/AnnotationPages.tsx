import React, { useEffect, useState } from 'react';
import { Alert, Box, Chip, Typography } from '@mui/material';
import LocalOfferIcon from '@mui/icons-material/LocalOffer';
import TuneIcon from '@mui/icons-material/Tune';
import { EntityCrudPage, StatusChip } from '../components/EntityCrud';
import { apiClient } from '../services/apiClient';
import { useTranslation } from '../i18n/useTranslation';

const TAGS = '/api/privacypa/tags';
const CF = '/api/privacypa/custom-fields';
const CUSTOM_ENTITIES = ['processing_activity', 'dpia', 'asset', 'party', 'contract', 'privacy_notice', 'project'];

// Elementi che portano un tag, limitati a quelli visibili all'utente (ricerca per tag, §39).
const TaggedEntities: React.FC<{ tag: any }> = ({ tag }) => {
  const { t } = useTranslation('privacypa');
  const [rows, setRows] = useState<any[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    apiClient.get<any[]>(`${TAGS}/${tag.id}/entities`).then(setRows).catch((err) => setError(err.message));
  }, [tag.id]);
  if (error) return <Alert severity="error">{error}</Alert>;
  if (!rows) return null;
  return (
    <>
      <Typography variant="body2" color="text.secondary">{tag.description || ''}</Typography>
      {rows.length === 0 && <Typography variant="body2" color="text.secondary">{t('annotations.no_tagged')}</Typography>}
      {rows.map((r) => (
        <Box key={`${r.entity_type}-${r.entity_id}`} sx={{ display: 'flex', gap: 1, alignItems: 'center' }}>
          <StatusChip enumName="annotationEntity" value={r.entity_type} />
          <Typography variant="body2">{[r.code, r.title].filter(Boolean).join(' — ') || r.entity_id}</Typography>
        </Box>
      ))}
    </>
  );
};

export const TagsPage: React.FC = () => (
  <EntityCrudPage
    page="tags" icon={<LocalOfferIcon color="primary" />} endpoint={TAGS} perm="tag" allowDelete
    fields={[
      { name: 'name', type: 'text', required: true }, { name: 'color', type: 'text', hint: 'wf.hints.tag_color' },
      { name: 'description', type: 'text' }, { name: 'active', type: 'boolean' },
    ]}
    toBody={(body, editing) => (editing ? body : { ...body, active: true })}
    columns={[
      { key: 'name', render: (r) => <Chip size="small" label={r.name} sx={r.color ? { bgcolor: r.color, color: '#fff' } : undefined} /> },
      { key: 'description', render: (r) => r.description || '—' }, { key: 'usage', render: (r) => r.usage },
      { key: 'active', render: (r) => (r.active ? '✓' : '—') },
    ]}
    detail={(row) => <TaggedEntities tag={row} />}
  />
);

const optionList = (text: unknown): string[] | null =>
  (Array.isArray(text) ? text : String(text || '').split(/[\n,]/)).map((o) => String(o).trim()).filter(Boolean);

export const CustomFieldsPage: React.FC = () => (
  <EntityCrudPage
    page="customFields" icon={<TuneIcon color="primary" />} endpoint={CF} perm="customfield" allowDelete
    fields={[
      { name: 'entity_type', type: 'select', options: CUSTOM_ENTITIES, enumName: 'annotationEntity', required: true, createOnly: true },
      { name: 'key', type: 'text', required: true, createOnly: true, hint: 'wf.hints.cf_key' },
      { name: 'label', type: 'text', required: true },
      { name: 'field_type', type: 'select', options: ['TEXT', 'NUMBER', 'DATE', 'BOOLEAN', 'SELECT'], enumName: 'customFieldType', required: true, createOnly: true },
      { name: 'options', type: 'textarea', hint: 'wf.hints.cf_options' },
      { name: 'required', type: 'boolean' }, { name: 'active', type: 'boolean' }, { name: 'position', type: 'number' },
    ]}
    toBody={(body, editing) => {
      const next = { ...body, options: body.options ? optionList(body.options) : null, position: body.position ?? 0 };
      return editing
        ? { ...next, entity_type: editing.entity_type, key: editing.key, field_type: editing.field_type }
        : { ...next, active: true };
    }}
    columns={[
      { key: 'entity_type', render: (r) => <StatusChip enumName="annotationEntity" value={r.entity_type} /> },
      { key: 'key', render: (r) => r.key }, { key: 'label', render: (r) => r.label },
      { key: 'field_type', render: (r) => <StatusChip enumName="customFieldType" value={r.field_type} /> },
      { key: 'required', render: (r) => (r.required ? '✓' : '—') }, { key: 'active', render: (r) => (r.active ? '✓' : '—') },
    ]}
  />
);
