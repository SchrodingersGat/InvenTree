import { t } from '@lingui/macro';
import type { SpotlightActionData } from '@mantine/spotlight';
import { IconHome, IconLink, IconPointer } from '@tabler/icons-react';
import { NavigateFunction } from 'react-router-dom';

import { useLocalState } from '../states/LocalState';
import { useUserState } from '../states/UserState';
import { aboutInvenTree, docLinks, licenseInfo, serverInfo } from './links';

export function getActions(navigate: NavigateFunction) {
  const setNavigationOpen = useLocalState((state) => state.setNavigationOpen);
  const { user } = useUserState();

  const actions: SpotlightActionData[] = [
    {
      id: 'home',
      label: t`Home`,
      description: `Go to the home page`,
      onClick: () => {}, // navigate(menuItems.home.link),
      leftSection: <IconHome size="1.2rem" />
    },
    {
      id: 'dashboard',
      label: t`Dashboard`,
      description: t`Go to the InvenTree dashboard`,
      onClick: () => {}, // navigate(menuItems.dashboard.link),
      leftSection: <IconLink size="1.2rem" />
    },
    {
      id: 'documentation',
      label: t`Documentation`,
      description: t`Visit the documentation to learn more about InvenTree`,
      onClick: () => (window.location.href = docLinks.faq),
      leftSection: <IconLink size="1.2rem" />
    },
    {
      id: 'about',
      label: t`About InvenTree`,
      description: t`About the InvenTree org`,
      onClick: () => aboutInvenTree(),
      leftSection: <IconLink size="1.2rem" />
    },
    {
      id: 'server-info',
      label: t`Server Information`,
      description: t`About this Inventree instance`,
      onClick: () => serverInfo(),
      leftSection: <IconLink size="1.2rem" />
    },
    {
      id: 'license-info',
      label: t`License Information`,
      description: t`Licenses for dependencies of the service`,
      onClick: () => licenseInfo(),
      leftSection: <IconLink size="1.2rem" />
    },
    {
      id: 'navigation',
      label: t`Open Navigation`,
      description: t`Open the main navigation menu`,
      onClick: () => setNavigationOpen(true),
      leftSection: <IconPointer size="1.2rem" />
    }
  ];

  // Staff actions
  user?.is_staff &&
    actions.push({
      id: 'admin-center',
      label: t`Admin Center`,
      description: t`Go to the Admin Center`,
      onClick: () => {}, /// navigate(menuItems['settings-admin'].link),}
      leftSection: <IconLink size="1.2rem" />
    });

  return actions;
}
