"""Direct API keys stay outside project/UI results and are scoped to each service."""
from test_chat_scroll import transcript_shell
from test_screenplay_ui import render_ready, visuals
from test_static_check_ui import click, plain


def test_project_key_save_blank_preserve_clear_scope_and_late_reply(tmp_path,monkeypatch):
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtTest import QTest

    from narrative.operations import Operations

    monkeypatch.setenv('QT_QUICK_CONTROLS_STYLE','Fusion')
    with transcript_shell(tmp_path,monkeypatch) as ui:
        ops=Operations.open(ui.bridge._project_path)
        path=str(ui.bridge._project_path)
        ui.window.setProperty('assistantOpen',False)
        ui.window.setProperty('currentPage','project_settings')
        page=ui.window.findChild(object,'projectSettingsPage')
        ui.wait(lambda:page.property('loaded') and not ui.window.property('busy'))
        secret=page.findChild(object,'projectApiKey')
        status=page.findChild(object,'projectApiKeyStatus')
        scroll=page.findChild(object,'projectSettingsScroll').property('contentItem')
        def bring(item):
            y=item.mapToItem(scroll,QPointF()).y()+scroll.property('contentY')
            scroll.setProperty('contentY',max(0,min(scroll.property('contentHeight')-scroll.height(),y-30)))
            render_ready(ui)
        def press(item):
            bring(item)
            click(ui,item)
        def save():
            old=len(ui.results)
            press(page.findChild(object,'projectSettingsSave'))
            ui.wait(lambda:any(kind=='project_settings' and payload.get('saved') for kind,payload in ui.results[old:]))
            ui.wait(lambda:not ui.window.property('busy'))
        assert not page.property('keyVisible')
        secret.setProperty('text','test-only-secret-never-in-result')
        assert secret.property('displayText')!='test-only-secret-never-in-result'
        press(page.findChild(object,'projectApiKeyVisible'))
        assert secret.property('displayText')=='test-only-secret-never-in-result'
        with monkeypatch.context() as failure:
            def reject(*args,**kwargs):
                raise ValueError('test-only-secret-never-in-result')
            failure.setattr(type(ops),'save_project_settings',reject)
            press(page.findChild(object,'projectSettingsSave'))
            ui.wait(lambda:'设置未保存' in page.property('message'))
            assert secret.property('text')=='test-only-secret-never-in-result'
            assert 'test-only-secret-never-in-result' not in str(ui.results)
        save()
        assert secret.property('text')=='' and not page.property('keyVisible')
        assert ops.provider_api_key_status()['local_available']
        assert '本机保存' in status.property('text')
        assert 'test-only-secret-never-in-result' not in str(ui.results)
        save()  # Empty input preserves the stored credential.
        assert ops.provider_api_key_status()['local_available']
        secret.setProperty('text','discard-on-address-change')
        address=next(item for item in visuals(page) if item.objectName()=='projectBaseUrl')
        bring(address)
        click(ui,address)
        QTest.keyClick(ui.window,Qt.Key.Key_A,Qt.KeyboardModifier.ControlModifier)
        for character in 'http://127.0.0.1:49221/v1':
            QTest.keyClick(ui.window,character)
        assert secret.property('text')==''
        ui.wait(lambda:page.property('keyStatusKnown'))
        assert not plain(page.property('keyStatus')).get('local_available')
        assert '本机保存 API Key' not in status.property('text')
        # An old settings response from this scope must not overwrite the editor.
        token=page.property('settingsToken')
        ui.bridge.resultReady.emit('project_settings',{'request_token':token-1,'project_path':path,'profile':{'base_url':'stale'},'extraction_granularity':'fine'})
        assert plain(page.property('draft'))['base_url']=='http://127.0.0.1:49221/v1'
        secret.setProperty('text','discard-on-provider-change')
        providers=plain(page.property('catalog'))
        old_provider=plain(page.property('draft'))['provider']
        target=next(i for i,row in enumerate(providers) if row['id']!=old_provider)
        provider=page.findChild(object,'projectProvider')
        press(provider)
        QTest.keyClick(ui.window,Qt.Key.Key_Home)
        for _ in range(target):
            QTest.keyClick(ui.window,Qt.Key.Key_Down)
        QTest.keyClick(ui.window,Qt.Key.Key_Return)
        ui.wait(lambda:page.property('loaded') and plain(page.property('draft'))['provider']==providers[target]['id'])
        assert secret.property('text')==''
        assert not plain(page.property('keyStatus')).get('local_available')
        page.load()
        ui.wait(lambda:page.property('loaded') and plain(page.property('keyStatus')).get('local_available'))
        press(page.findChild(object,'projectApiKeyClear'))
        save()
        assert not ops.provider_api_key_status()['local_available']
        secret.setProperty('text','unsaved-project-one')
        other=tmp_path/'other-key.nproj'
        Operations.create_graph_project(other)
        ui.bridge.openProject(str(other))
        ui.window.setProperty('currentPage','project_settings')
        ui.wait(lambda:page.property('boundProjectPath')==str(other) and page.property('loaded') and not ui.window.property('busy'))
        assert secret.property('text')==''
        ui.bridge.resultReady.emit('project_settings',{'request_token':page.property('settingsToken'),'project_path':path,'profile':{'model':'old-secret-scope'},'extraction_granularity':'fine','api_key_status':{'local_available':True}})
        assert plain(page.property('profile')).get('model')!='old-secret-scope'
        assert not plain(page.property('keyStatus')).get('local_available')


def test_explicit_credential_check_is_local_non_saving_and_scope_guarded(tmp_path,monkeypatch):
    import threading

    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtTest import QTest

    from narrative.operations import Operations

    monkeypatch.setenv('QT_QUICK_CONTROLS_STYLE','Fusion')
    with transcript_shell(tmp_path,monkeypatch) as ui:
        ops=Operations.open(ui.bridge._project_path)
        before=ops.load_project_settings()
        calls=[]
        started,release=threading.Event(),threading.Event()
        def check(self,profile=None,api_key=None,ignore_saved_key=False):
            calls.append({'model':profile['model'],'supplied':api_key,'ignore_saved_key':ignore_saved_key})
            started.set()
            assert release.wait(5), 'test release missing'
            return {'valid':True,'code':'valid','message':'当前模型配置可用','usage':{'prompt_tokens':1}}
        monkeypatch.setattr(type(ops),'check_model_credential',check)
        ui.window.setProperty('assistantOpen',False)
        ui.window.setProperty('currentPage','project_settings')
        page=ui.window.findChild(object,'projectSettingsPage')
        ui.wait(lambda:page.property('loaded') and not ui.window.property('busy'))
        secret=page.findChild(object,'projectApiKey')
        button=page.findChild(object,'projectApiKeyCheck')
        scroll=page.findChild(object,'projectSettingsScroll').property('contentItem')
        def press(item):
            y=item.mapToItem(scroll,QPointF()).y()+scroll.property('contentY')
            scroll.setProperty('contentY',max(0,min(scroll.property('contentHeight')-scroll.height(),y-30)))
            render_ready(ui)
            click(ui,item)
        secret.setProperty('text','only-for-check')
        try:
            press(button)
            ui.wait(started.is_set)
            assert page.property('keyChecking')
            assert not ui.window.property('busy')
            assert secret.property('enabled')
            model=next(item for item in visuals(page) if item.objectName()=='projectModel')
            press(model)
            QTest.keyClick(ui.window,Qt.Key.Key_End)
            QTest.keyClick(ui.window,'x')
            assert not page.property('keyChecking')
            release.set()
            ui.wait(lambda:any(kind=='project_key_check' for kind,payload in ui.results))
            assert not plain(page.property('keyCheckResult'))
            assert len(calls)==1  # Editing does not make a new paid request.
            press(button)
            ui.wait(lambda:plain(page.property('keyCheckResult')).get('valid') is True)
            assert '有效' in page.findChild(object,'projectApiKeyCheckResult').property('text')
            assert secret.property('text')=='only-for-check'
            assert ops.load_project_settings()==before
            assert 'only-for-check' not in str(ui.results)
            press(page.findChild(object,'projectApiKeyClear'))
            assert secret.property('text')==''
            press(button)
            ui.wait(lambda:len(calls)==3 and not page.property('keyChecking'))
            assert calls[-1]['ignore_saved_key'] and calls[-1]['supplied'] is None
            assert ops.load_project_settings()==before
        finally:
            release.set()
