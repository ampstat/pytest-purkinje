# -*- coding: utf-8 -*-

from __future__ import print_function
import pytest
from builtins import object
import websocket
import os
import logging
import time
import socket
try:
    import md5
except ImportError:
    from hashlib import md5
from purkinje_messages.message import(
    SessionStartedEvent,
    TestCaseFinishedEvent,
    SessionTerminatedEvent,
    ConnectionTerminationEvent)


VERDICT_MAP = {
    'passed': 'pass',
    'failed': 'fail',
    'skipped': 'skipped',
    'error': 'error'
}


def _log(fmt, *args):
    # TODO use print, logging or py.test facility if it exists
    fmt = '** testmon: {0} **'.format(fmt)
    print(fmt % args)


class TestMonitorPlugin(object):

    """py.test plugin for monitoring test progress and
       capturing results

       TODO graceful termination
    """
    # needs to be at class level because py.test uses
    # a new plugin instance when starting the test session
    # after collecting test cases
    tc_count = 0

    def __init__(self, websocket_url):
        self.reports = []
        self._websocket_url = websocket_url
        self._websocket = None
        self._test_cases = {}
        self._current_suite = None
        self._start_message_sent = False

        try:
            _log('Connecting to WebSocket %s', websocket_url)
            self._websocket = websocket.create_connection(websocket_url)
        except ValueError as e:
            _log('Invalid WebSocket URL: "%s"',
                 self._websocket_url)
        except Exception as e:
            _log('Error connecting to WebSocket at URL %s: %s',
                 self._websocket_url, e)

    def is_websocket_connected(self):
        return self._websocket is not None

    def suite_name(self):
        current_dir = os.getcwd()
        # current_dir_base = op.basename(current_dir)
        return '{0}: {1}'.format(socket.gethostname(),
                                 current_dir)

    def suite_hash(self):
        return md5(self.suite_name().encode('utf-8')).hexdigest()

    def send_event(self, event):
        """Send event via WebSocket connection.
           If there is no connection, the event will be dumped to the log
           only, so it is possible to run tests with purkinje enabled even
           if the server should be down
        """
        ser_event = None
        try:
            ser_event = event.serialize()

            if self._websocket:
                self._websocket.send(ser_event)
            else:
                _log('purkinje server not available; event: %s',
                     ser_event)
        except Exception as e:
            logging.exception(e)
            _log('Error while sending event "%s": %s',
                 ser_event or event.data, e)

    def pytest_sessionstart(self):
        _log('*** py.test session started ***')

    def _send_start_event(self):
        self._current_suite = self.suite_name()
        self.send_event(SessionStartedEvent(
            suite_name=self.suite_name(),
            suite_hash=self.suite_hash(),
            tc_count=TestMonitorPlugin.tc_count
        ))

    def pytest_sessionfinish(self):
        _log('*** py.test session finished ***')
        self.send_event(SessionTerminatedEvent(
            suite_hash=self.suite_hash()
        ))
        self.send_event(ConnectionTerminationEvent(
            suite_hash=self.suite_hash()
        ))

    # def pytest_collection_modifyitems(self, session, config, items):
    #     print('pytest_collection_modifyitems: {0} {1} {2}'.format(session,
    #                                                               config,
    #                                                               items))

    def pytest_collectstart(self, collector):
        _log('pytest_collectstart: %s', collector)

    def _is_relevant_tc(self, tc):
        return isinstance(tc, pytest.Item)

    def pytest_collectreport(self, report):
        _log('pytest_collectreport: %s', report)

        test_funcs = [x
                      for x
                      in report.result
                      if self._is_relevant_tc(x)
                      ]
        TestMonitorPlugin.tc_count += len(test_funcs)
        # import pdb; pdb.set_trace()

    def pytest_runtest_logreport(self, report):
        # _log('pytest_runtest_logreport: %s', report)

        # _log('######## self._test_cases: %s %s %s',
        #          report.nodeid, report.when, self._test_cases)

        tc_file = report.fspath
        tc_components = report.nodeid.split('::')

        if len(tc_components) > 1:
            tc_name = tc_components[1]
        else:
            # try to find a human-readable name
            # for the test case
            if 'pep8' in report.keywords:
                tc_name = 'PEP8'
                # when found in cache, the py.test pep8 plugin
                # reports skipped tests for each unchanged
                # Python files. For testing, the py.test
                # option --clearcache may be used to force
                # execution of pep8 checks on each file
            else:
                tc_name = str(report.keywords)

        rep_key = report.nodeid

        if report.when == 'setup':
            self._test_cases[rep_key] = time.time()

        if ((report.when != 'call') and not
            (report.when == 'setup' and
             report.outcome == 'skipped')):
            return

        if report.when == 'call':
            if rep_key not in self._test_cases:
                _log('Test case {0} not found'.format(tc_name))
                return
            duration = int((time.time() -
                            self._test_cases[rep_key]) * 1000)
        else:
            duration = 0

        if not self._start_message_sent:
            self._send_start_event()
            self._start_message_sent = True

        self.send_event(TestCaseFinishedEvent(
            name=tc_name,
            file=tc_file,
            verdict=VERDICT_MAP[report.outcome],
            duration=duration,
            suite_hash=self.suite_hash()))
        self.reports.append(report)


def pytest_addoption(parser):
    parser.addoption(
        '--websocket_host',
        nargs='?',
        default='localhost',
        const=True,
        dest='websocket_host',
        help='WebSocket hostname or IP address of purkinje server'
    )

    parser.addoption(
        '--websocket_port',
        nargs='?',
        default=5000,
        const=True,
        dest='websocket_port',
        help='WebSocket TCP ort of purkinje server'
    )


def pytest_configure(config):
    websocket_host = config.getoption('websocket_host')
    websocket_port = config.getoption('websocket_port')
    websocket_url = 'ws://{}:{}/event'.format(websocket_host, websocket_port)
    config.pluginmanager.register(TestMonitorPlugin(websocket_url))
