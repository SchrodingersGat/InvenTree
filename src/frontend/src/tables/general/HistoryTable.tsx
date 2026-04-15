import type { ModelType } from '@lib/enums/ModelType';
import useTable from '@lib/hooks/UseTable';
import { ApiEndpoints, type TableColumn, apiUrl } from '@lib/index';
import { t } from '@lingui/core/macro';
import { useMemo } from 'react';
import { formatDate } from '../../defaults/formatters';
import { UserColumn } from '../ColumnRenderers';
import { InvenTreeTable } from '../InvenTreeTable';

export default function HistoryTable({
  modelType,
  modelId
}: Readonly<{
  modelType: ModelType;
  modelId: number;
}>) {
  const table = useTable('history');

  const tableColumns: TableColumn[] = useMemo(() => {
    return [
      {
        accessor: 'timestamp',
        title: t`Timestamp`,
        sortable: true,
        render: (record: any) => {
          return formatDate(record.timestamp, { showTime: true });
        }
      },
      UserColumn({
        accessor: 'user_detail',
        sortable: true,
        switchable: true
      })
    ];
  }, []);

  return (
    <InvenTreeTable
      url={apiUrl(ApiEndpoints.audit_log_list)}
      tableState={table}
      columns={tableColumns}
      props={{
        params: {
          model_type: modelType,
          model_id: modelId
        }
      }}
    />
  );
}
