import type { ModelType } from '@lib/enums/ModelType';
import useTable from '@lib/hooks/UseTable';
import {
  ApiEndpoints,
  type TableColumn,
  type TableFilter,
  type TableFilterChoice,
  apiUrl
} from '@lib/index';
import { t } from '@lingui/core/macro';
import { Badge, Group, Table, Text } from '@mantine/core';
import { IconArrowRight } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import { useMemo } from 'react';
import { useApi } from '../../contexts/ApiContext';
import { formatDate } from '../../defaults/formatters';
import { UserColumn } from '../ColumnRenderers';
import { UserFilter } from '../Filter';
import { InvenTreeTable } from '../InvenTreeTable';

enum HistoryActionType {
  CREATE = 0,
  UPDATE = 1,
  DELETE = 2
}

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
  const api = useApi();

  const tableKey = useMemo(() => {
    if (modelType && modelId) {
      return 'history-model';
    } else {
      // Global history view
      return 'history-global';
    }
  }, [modelType, modelId]);

  const table = useTable(tableKey);

  const actionChoices: TableFilterChoice[] = useMemo(() => {
    return [
      { value: HistoryActionType.CREATE.toString(), label: t`Create` },
      { value: HistoryActionType.UPDATE.toString(), label: t`Update` },
      { value: HistoryActionType.DELETE.toString(), label: t`Delete` }
    ];
  }, []);

  // Fetch available ContentType options for filtering / rendering
  const contentTypes = useQuery({
    queryKey: ['auditable-content-types'],
    enabled: !modelType || !modelId, // Only fetch if we're in the global history view
    queryFn: async () => {
      return api
        .get(apiUrl(ApiEndpoints.content_type_list), {
          params: {
            auditable: true
          }
        })
        .then((res) => res.data);
    }
  });

  // Generate a list of available model options for filtering
  const contentTypeOptions: TableFilterChoice[] = useMemo(() => {
    // Ignore if we're already filtering by a specific model type / ID
    if (!!modelType && !!modelId) {
      return [];
    }

    return (
      (contentTypes?.data ?? []).map((ct: any) => {
        return {
          value: ct.pk.toString(),
          label: ct.app_labeled_name
        };
      }) ?? []
    );
  }, [modelType, modelId, contentTypes.data]);

  // Generate a list of available app_label options for filtering
  const appLabelOptions: TableFilterChoice[] = useMemo(() => {
    // Ignore if we're already filtering by a specific model type / ID
    if (!!modelType && !!modelId) {
      return [];
    }

    const appLabels = new Set(
      (contentTypes?.data ?? []).map((ct: any) => ct.app_label)
    );

    return Array.from(appLabels).map((appLabel) => {
      return {
        value: `${appLabel}`,
        label: `${appLabel}`
      };
    });
  }, [modelType, modelId, contentTypes.data]);

  const tableColumns: TableColumn[] = useMemo(() => {
    return [
      {
        accessor: 'content_type',
        title: t`Model`,
        sortable: true,
        render: (record: any) => {
          // Lookup the appropriate content type for this record
          const ct = contentTypeOptions.find(
            (ct) => ct.value === record.content_type.toString()
          );

          return (
            <Group justify='space-between'>
              <Text size='sm'>{ct?.label ?? record.content_type}</Text>
              <Badge size='xs'>
                {t`ID`}: {record.object_id}
              </Badge>
            </Group>
          );
        }
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
        accessor: 'changes',
        title: t`Changes`,
        sortable: false,
        switchable: false,
        render: (record: any) => {
          return <ChangeGroup record={record} />;
        }
      }
    ];
  }, [contentTypeOptions]);

  const tableFilters: TableFilter[] = useMemo(() => {
    return [
      UserFilter({
        name: 'actor',
        label: t`User`,
        description: t`Filter by user who made the change`
      }),
      {
        name: 'action',
        label: t`Action`,
        description: t`Filter by type of change`,
        type: 'choice',
        choices: actionChoices
      },
      {
        name: 'model_id',
        label: t`Model ID`,
        description: t`Filter by model ID`,
        type: 'number',
        active: !modelId
      },
      {
        name: 'content_type',
        label: t`Model Type`,
        description: t`Filter by model type`,
        type: 'choice',
        choices: contentTypeOptions,
        active: contentTypeOptions.length > 0
      },
      {
        name: 'app_label',
        label: t`App Label`,
        description: t`Filter by app label`,
        type: 'choice',
        choices: appLabelOptions,
        active: appLabelOptions.length > 0
      }
    ];
  }, [actionChoices, contentTypeOptions, appLabelOptions, modelId]);

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
