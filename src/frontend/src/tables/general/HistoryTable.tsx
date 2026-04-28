import type { ModelType } from '@lib/enums/ModelType';
import useTable from '@lib/hooks/UseTable';
import {
  ApiEndpoints,
  type TableColumn,
  type TableFilter,
  apiUrl
} from '@lib/index';
import { t } from '@lingui/core/macro';
import { Badge, Group, Table, Text } from '@mantine/core';
import { IconArrowRight } from '@tabler/icons-react';
import { useMemo } from 'react';
import { formatDate } from '../../defaults/formatters';
import { UserColumn } from '../ColumnRenderers';
import { UserFilter } from '../Filter';
import { InvenTreeTable } from '../InvenTreeTable';

function ChangeGroup({
  record
}: Readonly<{
  record: any;
}>) {
  const action = record.action ?? null;
  const changes = record.changes ?? {};

  // TODO: Use an enum here
  if (action === 0) {
    return <Badge color='green'>{t`Item Created`}</Badge>;
  }

  // TODO: Use an enum here
  if (action === 2) {
    return <Badge color='red'>{t`Item Deleted`}</Badge>;
  }

  return (
    <Table striped withColumnBorders>
      <Table.Tbody>
        {Object.keys(changes).map((field) => {
          const change = changes[field];
          const oldValue = change?.[0] ?? null;
          const newValue = change?.[1] ?? null;

          return (
            <Table.Tr key={field}>
              <Table.Th>
                <Text size='sm'>{field}</Text>
              </Table.Th>
              <Table.Td>
                <Group justify='space-apart' grow>
                  {oldValue != null && <Text size='xs'>{oldValue}</Text>}
                  {oldValue != null && newValue != null && (
                    <IconArrowRight size={16} />
                  )}
                  {newValue != null && <Text size='xs'>{newValue}</Text>}
                </Group>
              </Table.Td>
            </Table.Tr>
          );
        })}
      </Table.Tbody>
    </Table>
  );
}

export default function HistoryTable({
  modelType,
  modelId
}: Readonly<{
  modelType?: ModelType;
  modelId?: number;
}>) {
  const table = useTable('history');

  const tableColumns: TableColumn[] = useMemo(() => {
    return [
      {
        accessor: 'content_type',
        title: t`Model`,
        sortable: true
      },
      {
        accessor: 'object_id',
        title: t`Object ID`,
        sortable: true
      },
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
      }),
      {
        accessor: 'action',
        title: t`Action`,
        sortable: true,
        switchable: true,
        render: (record: any) => {
          // TODO
          return record.action;
        }
      },
      {
        accessor: 'changes',
        title: t`Changes`,
        sortable: false,
        switchable: false,
        render: (record: any) => {
          return <ChangeGroup record={record} />;
        }
      }
    ];
  }, []);

  const tableFilters: TableFilter[] = useMemo(() => {
    return [
      UserFilter({
        name: 'actor',
        label: t`User`,
        description: t`Filter by user who made the change`
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
        },
        tableFilters: tableFilters
      }}
    />
  );
}
