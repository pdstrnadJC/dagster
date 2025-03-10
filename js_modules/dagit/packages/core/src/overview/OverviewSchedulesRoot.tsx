import {gql, useQuery} from '@apollo/client';
import {
  Alert,
  Box,
  Button,
  Colors,
  Dialog,
  DialogFooter,
  Heading,
  NonIdealState,
  PageHeader,
  Spinner,
  TextInput,
  Tooltip,
} from '@dagster-io/ui';
import * as React from 'react';

import {PYTHON_ERROR_FRAGMENT} from '../app/PythonErrorFragment';
import {PythonErrorInfo} from '../app/PythonErrorInfo';
import {useQueryRefreshAtInterval, FIFTEEN_SECONDS} from '../app/QueryRefresh';
import {useTrackPageView} from '../app/analytics';
import {useDocumentTitle} from '../hooks/useDocumentTitle';
import {useQueryPersistedState} from '../hooks/useQueryPersistedState';
import {useSelectionReducer} from '../hooks/useSelectionReducer';
import {INSTANCE_HEALTH_FRAGMENT} from '../instance/InstanceHealthFragment';
import {RepoFilterButton} from '../instance/RepoFilterButton';
import {INSTIGATION_STATE_FRAGMENT} from '../instigation/InstigationUtils';
import {UnloadableSchedules} from '../instigation/Unloadable';
import {ScheduleBulkActionMenu} from '../schedules/ScheduleBulkActionMenu';
import {SchedulerInfo} from '../schedules/SchedulerInfo';
import {SchedulesCheckAll} from '../schedules/SchedulesCheckAll';
import {filterPermissionedSchedules} from '../schedules/filterPermissionedSchedules';
import {makeScheduleKey} from '../schedules/makeScheduleKey';
import {WorkspaceContext} from '../workspace/WorkspaceContext';
import {buildRepoAddress} from '../workspace/buildRepoAddress';
import {repoAddressAsHumanString} from '../workspace/repoAddressAsString';
import {RepoAddress} from '../workspace/types';

import {BASIC_INSTIGATION_STATE_FRAGMENT} from './BasicInstigationStateFragment';
import {OverviewScheduleTable} from './OverviewSchedulesTable';
import {OverviewTabs} from './OverviewTabs';
import {sortRepoBuckets} from './sortRepoBuckets';
import {BasicInstigationStateFragment} from './types/BasicInstigationStateFragment.types';
import {
  OverviewSchedulesQuery,
  OverviewSchedulesQueryVariables,
  UnloadableSchedulesQuery,
  UnloadableSchedulesQueryVariables,
} from './types/OverviewSchedulesRoot.types';
import {visibleRepoKeys} from './visibleRepoKeys';

export const OverviewSchedulesRoot = () => {
  useTrackPageView();
  useDocumentTitle('Overview | Schedules');

  const {allRepos, visibleRepos} = React.useContext(WorkspaceContext);
  const repoCount = allRepos.length;
  const [searchValue, setSearchValue] = useQueryPersistedState<string>({
    queryKey: 'search',
    defaults: {search: ''},
  });

  const queryResultOverview = useQuery<OverviewSchedulesQuery, OverviewSchedulesQueryVariables>(
    OVERVIEW_SCHEDULES_QUERY,
    {
      fetchPolicy: 'network-only',
      notifyOnNetworkStatusChange: true,
    },
  );
  const {data, loading} = queryResultOverview;

  const refreshState = useQueryRefreshAtInterval(queryResultOverview, FIFTEEN_SECONDS);

  const repoBuckets = React.useMemo(() => {
    const visibleKeys = visibleRepoKeys(visibleRepos);
    return buildBuckets(data).filter(({repoAddress}) =>
      visibleKeys.has(repoAddressAsHumanString(repoAddress)),
    );
  }, [data, visibleRepos]);

  const sanitizedSearch = searchValue.trim().toLocaleLowerCase();
  const anySearch = sanitizedSearch.length > 0;

  const filteredBySearch = React.useMemo(() => {
    const searchToLower = sanitizedSearch.toLocaleLowerCase();
    return repoBuckets
      .map(({repoAddress, schedules}) => ({
        repoAddress,
        schedules: schedules.filter(({name}) => name.toLocaleLowerCase().includes(searchToLower)),
      }))
      .filter(({schedules}) => schedules.length > 0);
  }, [repoBuckets, sanitizedSearch]);

  // Collect all schedules across visible code locations that the viewer has permission
  // to start or stop.
  const allPermissionedSchedules = React.useMemo(() => {
    return repoBuckets
      .map(({repoAddress, schedules}) => {
        return schedules.filter(filterPermissionedSchedules).map(({name, scheduleState}) => ({
          repoAddress,
          scheduleName: name,
          scheduleState,
        }));
      })
      .flat();
  }, [repoBuckets]);

  // Build a list of keys from the permissioned schedules for use in checkbox state.
  // This includes collapsed code locations.
  const allPermissionedScheduleKeys = React.useMemo(() => {
    return allPermissionedSchedules.map(({repoAddress, scheduleName}) =>
      makeScheduleKey(repoAddress, scheduleName),
    );
  }, [allPermissionedSchedules]);

  const [{checkedIds: checkedKeys}, {onToggleFactory, onToggleAll}] = useSelectionReducer(
    allPermissionedScheduleKeys,
  );

  // Filter to find keys that are visible given any text search.
  const permissionedKeysOnScreen = React.useMemo(() => {
    const filteredKeys = new Set(
      filteredBySearch
        .map(({repoAddress, schedules}) => {
          return schedules.map(({name}) => makeScheduleKey(repoAddress, name));
        })
        .flat(),
    );
    return allPermissionedScheduleKeys.filter((key) => filteredKeys.has(key));
  }, [allPermissionedScheduleKeys, filteredBySearch]);

  // Determine the list of schedule objects that have been checked by the viewer.
  // These are the schedules that will be operated on by the bulk start/stop action.
  const checkedSchedules = React.useMemo(() => {
    const checkedKeysOnScreen = new Set(
      permissionedKeysOnScreen.filter((key: string) => checkedKeys.has(key)),
    );
    return allPermissionedSchedules.filter(({repoAddress, scheduleName}) => {
      return checkedKeysOnScreen.has(makeScheduleKey(repoAddress, scheduleName));
    });
  }, [permissionedKeysOnScreen, allPermissionedSchedules, checkedKeys]);

  const viewerHasAnyInstigationPermission = allPermissionedScheduleKeys.length > 0;
  const checkedCount = checkedSchedules.length;

  const content = () => {
    if (loading && !data) {
      return (
        <Box flex={{direction: 'row', justifyContent: 'center'}} style={{paddingTop: '100px'}}>
          <Box flex={{direction: 'row', alignItems: 'center', gap: 16}}>
            <Spinner purpose="body-text" />
            <div style={{color: Colors.Gray600}}>Loading schedules…</div>
          </Box>
        </Box>
      );
    }

    const anyReposHidden = allRepos.length > visibleRepos.length;

    if (!filteredBySearch.length) {
      if (anySearch) {
        return (
          <Box padding={{top: 20}}>
            <NonIdealState
              icon="search"
              title="No matching schedules"
              description={
                anyReposHidden ? (
                  <div>
                    No schedules matching <strong>{searchValue}</strong> were found in the selected
                    code locations
                  </div>
                ) : (
                  <div>
                    No schedules matching <strong>{searchValue}</strong> were found in your
                    definitions
                  </div>
                )
              }
            />
          </Box>
        );
      }

      return (
        <Box padding={{top: 20}}>
          <NonIdealState
            icon="search"
            title="No schedules"
            description={
              anyReposHidden
                ? 'No schedules were found in the selected code locations'
                : 'No schedules were found in your definitions'
            }
          />
        </Box>
      );
    }

    return (
      <OverviewScheduleTable
        headerCheckbox={
          viewerHasAnyInstigationPermission ? (
            <SchedulesCheckAll
              checkedCount={checkedCount}
              totalCount={permissionedKeysOnScreen.length}
              onToggleAll={onToggleAll}
            />
          ) : undefined
        }
        repos={filteredBySearch}
        checkedKeys={checkedKeys}
        onToggleCheckFactory={onToggleFactory}
      />
    );
  };

  return (
    <Box flex={{direction: 'column'}} style={{height: '100%', overflow: 'hidden'}}>
      <PageHeader
        title={<Heading>Overview</Heading>}
        tabs={<OverviewTabs tab="schedules" refreshState={refreshState} />}
      />
      <Box
        padding={{horizontal: 24, vertical: 16}}
        flex={{direction: 'row', alignItems: 'center', justifyContent: 'space-between'}}
      >
        <Box flex={{direction: 'row', gap: 12}}>
          {repoCount > 0 ? <RepoFilterButton /> : null}
          <TextInput
            icon="search"
            value={searchValue}
            onChange={(e) => {
              setSearchValue(e.target.value);
              onToggleAll(false);
            }}
            placeholder="Filter by schedule name…"
            style={{width: '340px'}}
          />
        </Box>
        <Tooltip
          content="You do not have permission to start or stop these schedules"
          canShow={!viewerHasAnyInstigationPermission}
          placement="top-end"
          useDisabledButtonTooltipFix
        >
          <ScheduleBulkActionMenu
            schedules={checkedSchedules}
            onDone={() => refreshState.refetch()}
          />
        </Tooltip>
      </Box>
      {loading && !repoCount ? (
        <Box padding={64}>
          <Spinner purpose="page" />
        </Box>
      ) : (
        <>
          {data?.unloadableInstigationStatesOrError.__typename === 'InstigationStates' ? (
            <UnloadableSchedulesAlert
              count={data.unloadableInstigationStatesOrError.results.length}
            />
          ) : null}
          <SchedulerInfo
            daemonHealth={data?.instance.daemonHealth}
            padding={{vertical: 16, horizontal: 24}}
            border={{side: 'top', width: 1, color: Colors.KeylineGray}}
          />
          {content()}
        </>
      )}
    </Box>
  );
};

const UnloadableSchedulesAlert: React.FC<{
  count: number;
}> = ({count}) => {
  const [isOpen, setIsOpen] = React.useState(false);

  if (!count) {
    return null;
  }

  const title = count === 1 ? '1 unloadable schedule' : `${count} unloadable schedules`;

  return (
    <>
      <Box
        padding={{vertical: 16, horizontal: 24}}
        border={{side: 'top', width: 1, color: Colors.KeylineGray}}
      >
        <Alert
          intent="warning"
          title={title}
          description={
            <Box flex={{direction: 'column', gap: 12, alignItems: 'flex-start'}}>
              <div>
                Schedules were previously started but now cannot be loaded. They may be part of a
                code locations that no longer exist. You can turn them off, but you cannot turn them
                back on.
              </div>
              <Button onClick={() => setIsOpen(true)}>
                {count === 1 ? 'View unloadable schedule' : 'View unloadable schedules'}
              </Button>
            </Box>
          }
        />
      </Box>
      <Dialog
        isOpen={isOpen}
        title="Unloadable schedules"
        style={{width: '90vw', maxWidth: '1200px'}}
      >
        <Box padding={{bottom: 8}}>
          <UnloadableScheduleDialog />
        </Box>
        <DialogFooter>
          <Button intent="primary" onClick={() => setIsOpen(false)}>
            Done
          </Button>
        </DialogFooter>
      </Dialog>
    </>
  );
};

const UnloadableScheduleDialog: React.FC = () => {
  const {data} = useQuery<UnloadableSchedulesQuery, UnloadableSchedulesQueryVariables>(
    UNLOADABLE_SCHEDULES_QUERY,
  );
  if (!data) {
    return <Spinner purpose="section" />;
  }

  if (data?.unloadableInstigationStatesOrError.__typename === 'InstigationStates') {
    return (
      <UnloadableSchedules
        scheduleStates={data.unloadableInstigationStatesOrError.results}
        showSubheading={false}
      />
    );
  }

  return <PythonErrorInfo error={data?.unloadableInstigationStatesOrError} />;
};

type RepoBucket = {
  repoAddress: RepoAddress;
  schedules: {name: string; scheduleState: BasicInstigationStateFragment}[];
};

const buildBuckets = (data?: OverviewSchedulesQuery): RepoBucket[] => {
  if (data?.workspaceOrError.__typename !== 'Workspace') {
    return [];
  }

  const entries = data.workspaceOrError.locationEntries.map((entry) => entry.locationOrLoadError);

  const buckets = [];

  for (const entry of entries) {
    if (entry?.__typename !== 'RepositoryLocation') {
      continue;
    }

    for (const repo of entry.repositories) {
      const {name, schedules} = repo;
      const repoAddress = buildRepoAddress(name, entry.name);
      const scheduleNames = schedules.map(({name, scheduleState}) => ({name, scheduleState}));

      if (scheduleNames.length > 0) {
        buckets.push({
          repoAddress,
          schedules: scheduleNames,
        });
      }
    }
  }

  return sortRepoBuckets(buckets);
};

const OVERVIEW_SCHEDULES_QUERY = gql`
  query OverviewSchedulesQuery {
    workspaceOrError {
      ... on Workspace {
        id
        locationEntries {
          id
          locationOrLoadError {
            ... on RepositoryLocation {
              id
              name
              repositories {
                id
                name
                schedules {
                  id
                  name
                  description
                  scheduleState {
                    id
                    ...BasicInstigationStateFragment
                  }
                }
              }
            }
            ...PythonErrorFragment
          }
        }
      }
      ...PythonErrorFragment
    }
    unloadableInstigationStatesOrError(instigationType: SCHEDULE) {
      ... on InstigationStates {
        results {
          id
        }
      }
    }
    instance {
      id
      ...InstanceHealthFragment
    }
  }

  ${BASIC_INSTIGATION_STATE_FRAGMENT}
  ${PYTHON_ERROR_FRAGMENT}
  ${INSTANCE_HEALTH_FRAGMENT}
`;

const UNLOADABLE_SCHEDULES_QUERY = gql`
  query UnloadableSchedulesQuery {
    unloadableInstigationStatesOrError(instigationType: SCHEDULE) {
      ... on InstigationStates {
        results {
          id
          ...InstigationStateFragment
        }
      }
      ...PythonErrorFragment
    }
  }

  ${INSTIGATION_STATE_FRAGMENT}
  ${PYTHON_ERROR_FRAGMENT}
`;
