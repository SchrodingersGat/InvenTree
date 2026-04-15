import type { ModelType } from '@lib/enums/ModelType';
import { IconHistory } from '@tabler/icons-react';
import HistoryTable from '../../tables/general/HistoryTable';
import type { PanelType } from './Panel';

export default function HistoryPanel({
  model_type,
  model_id
}: Readonly<{
  model_type: ModelType;
  model_id: number;
}>): PanelType {
  // const user = useUserState();
  // const { server } = useServerApiState();

  return {
    name: 'history',
    label: 'History',
    icon: <IconHistory />,
    content: <HistoryTable modelType={model_type} modelId={model_id} />
    // hidden: !user.isStaff() || !server?.auditlog_enabled,
  };
}
