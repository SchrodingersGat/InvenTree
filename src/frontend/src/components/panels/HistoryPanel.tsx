import type { ModelType } from '@lib/enums/ModelType';
import { IconReport } from '@tabler/icons-react';
import { useServerApiState } from '../../states/ServerApiState';
import { useUserState } from '../../states/UserState';
import HistoryTable from '../../tables/general/HistoryTable';
import type { PanelType } from './Panel';

export default function HistoryPanel({
  model_type,
  model_id
}: Readonly<{
  model_type: ModelType;
  model_id: number;
}>): PanelType {
  const user = useUserState.getState();
  const { server } = useServerApiState.getState();

  return {
    name: 'history',
    label: 'History',
    icon: <IconReport />,
    hidden: !user.isStaff() || !server?.auditlog_enabled,
    content: <HistoryTable modelType={model_type} modelId={model_id} />
  };
}
