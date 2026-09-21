import React, { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Badge, IconButton, List, ListItemButton, ListItemText, Popover, Tooltip, Typography } from '@mui/material';
import NotificationsIcon from '@mui/icons-material/Notifications';
import { apiClient } from '../services/apiClient';
import { useTranslation } from '../i18n/useTranslation';

interface Item { kind: string; title: string; href?: string }

export const NotificationBell: React.FC = () => {
  const { t } = useTranslation('privacypa');
  const navigate = useNavigate();
  const [items, setItems] = useState<Item[]>([]);
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);

  const load = () => {
    apiClient.get<{ items: Item[] }>('/api/privacypa/notifications')
      .then((r) => setItems(r.items || []))
      .catch(() => setItems([]));
  };

  useEffect(() => {
    load();
    const id = window.setInterval(load, 60000);
    return () => window.clearInterval(id);
  }, []);

  return (
    <>
      <Tooltip title={t('notifications.title')}>
        <IconButton color="inherit" onClick={(e) => { setAnchor(e.currentTarget); load(); }} aria-label={t('notifications.title')}>
          <Badge badgeContent={items.length} color="error" max={99}>
            <NotificationsIcon />
          </Badge>
        </IconButton>
      </Tooltip>
      <Popover open={Boolean(anchor)} anchorEl={anchor} onClose={() => setAnchor(null)} anchorOrigin={{ vertical: 'bottom', horizontal: 'right' }}>
        <Typography variant="subtitle2" sx={{ px: 2, pt: 1.5 }}>{t('notifications.title')}</Typography>
        {items.length === 0 ? (
          <Typography variant="body2" color="text.secondary" sx={{ px: 2, py: 2, minWidth: 280 }}>
            {t('notifications.empty')}
          </Typography>
        ) : (
          <List dense sx={{ minWidth: 320, maxWidth: 380 }}>
            {items.map((item, i) => (
              <ListItemButton key={`${item.kind}-${i}`} onClick={() => { setAnchor(null); if (item.href) navigate(item.href); }}>
                <ListItemText primary={item.title} />
              </ListItemButton>
            ))}
          </List>
        )}
      </Popover>
    </>
  );
};
