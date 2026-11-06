import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Dialog {
    id: editor
    SubmissionState{id:contentSubmission;owner:editor;operation:"save_creative_agreement";target:({screenplay_id:editor.screenplayId||null});onRestoreRequested:function(a){editor.projectRevision=a.expected_project_revision;editor.scriptRevision=a.expected_script_revision;titleField.text=a.title||"";languageField.text=a.language||"zh-CN";editor.original=Object.assign({},editor.original,{project_defaults:a.project_defaults||{},script_overrides:a.script_overrides||{}})}}

    objectName:"creativeAgreementEditor"
    property var host
    property var original:({})
    property var latest:({})
    property string projectPath:""
    property string screenplayId:""
    property int sequence:0
    property string token:""
    property bool loaded:false
    property bool busy:false
    property bool preserveDraft:false
    property bool showLatest:false
    property int projectRevision:0
    property var scriptRevision:null
    property string message:""
    readonly property bool scriptScope:scopeChoice.currentIndex===1&&!!screenplayId
    readonly property bool validScope:projectPath===(host.app.project.path||"")
    title:"剧本创作约定"
    modal:true;anchors.centerIn:parent;width:Math.min(920,host.width-28);height:Math.min(780,host.height-24)
    closePolicy:busy?Popup.NoAutoClose:Popup.CloseOnEscape
    function nextToken(){sequence++;token="creative-"+(host.structureOnly?"structure":"writing")+"-"+sequence}
    function openFor(id) {
        if(visible&&!contentSubmission.restoring)contentSubmission.begin();
        projectPath=host.app.project.path||"";screenplayId=id===undefined?(host.screenplay.id||""):(id||"")
        loaded=false;showLatest=false;original=({});latest=({});message="";scopeChoice.currentIndex=screenplayId?1:0
        open();read(false)
    }
    function read(keep) {if(!validScope)return;preserveDraft=keep;busy=true;message="";nextToken();var args={_view_request_token:token};if(screenplayId)args.screenplay_id=screenplayId;host.act("load_creative_agreement",args)}
    function collect() {
        var defaults={},overrides={}
        for(var i=0;i<fields.count;i++){var row=fields.itemAt(i);defaults[row.key]=row.projectText;if(!row.inherits)overrides[row.key]=row.overrideText}
        return {project_defaults:defaults,script_overrides:overrides}
    }
    function save() {
        if(!loaded||busy||!validScope)return
        var value=collect(),args={project_defaults:value.project_defaults,expected_project_revision:projectRevision,reason:"编辑创作约定",_view_request_token:""}
        if(screenplayId){args.screenplay_id=screenplayId;args.script_overrides=value.script_overrides;args.expected_script_revision=scriptRevision;args.title=titleField.text;args.language=languageField.text}
        nextToken();args._view_request_token=token;busy=true;message="";host.act("save_creative_agreement",args)
    }
    Connections {
        target:bridge
        function onResultReady(kind,payload) {
            if(kind!=="screenplay"||["load_creative_agreement","save_creative_agreement"].indexOf(payload.operation)<0||payload.request_token!==editor.token||!editor.visible||!editor.validScope)return
            var value=payload.result||{}
            if((value.screenplay_id||"")!==editor.screenplayId)return
            editor.busy=false;editor.projectRevision=value.project_revision;editor.scriptRevision=value.script_revision;editor.latest=value
            if(!editor.preserveDraft||payload.operation==="save_creative_agreement"){
                editor.loaded=false;editor.original=JSON.parse(JSON.stringify(value));titleField.text=value.title||"";languageField.text=value.language||"zh-CN";editor.loaded=true;editor.showLatest=false
            }else{editor.showLatest=true}
            editor.preserveDraft=false
            if(payload.operation==="save_creative_agreement")editor.host.load()
            editor.message=payload.operation==="save_creative_agreement"?"创作约定已保存；工程默认与当前剧本覆盖已一并应用。":editor.showLatest?"已读取最新版本，输入草稿保留。请比较差异后保存，或逐项采用最新值。":""
        }
        function onErrorOccurred(kind,text){if(kind==="screenplay"&&editor.visible&&editor.busy){editor.busy=false;editor.message=text}}
    }
    Connections {target:host.app;function onProjectChanged(){editor.sequence++;editor.token="";editor.close();editor.loaded=false;editor.busy=false;editor.original=({});editor.latest=({})}}
    contentItem:ColumnLayout {
        SubmissionFeedback{state:contentSubmission}

        Label {text:"工程默认供全部剧本继承；当前剧本覆盖只对这一部生效。继承与明确留空不同，保存会一并提交本窗口的全部更改。";wrapMode:Text.Wrap;Layout.fillWidth:true}
        RowLayout {
            ComboBox {id:scopeChoice;objectName:"creativeAgreementScope";model:editor.screenplayId?["工程默认","当前剧本覆盖"]:["工程默认"]}
            Label {text:editor.screenplayId?(editor.original.title||"当前剧本")+" · "+editor.screenplayId:"尚未选择剧本，可编辑工程默认";elide:Text.ElideRight;Layout.fillWidth:true}
            CheckBox {id:preview;objectName:"creativeAgreementPreview";text:"预览有效值"}
        }
        Label {visible:!!editor.message;text:editor.message;wrapMode:Text.Wrap;Layout.fillWidth:true;color:host.t.warn}
        ScrollView {id:agreementScroll;contentWidth:availableWidth;Layout.fillWidth:true;Layout.fillHeight:true;clip:true
            ColumnLayout {width:agreementScroll.availableWidth;enabled:editor.loaded&&!editor.busy
                GroupBox {visible:editor.scriptScope&&!preview.checked;title:"剧本基本信息";Layout.fillWidth:true
                    ColumnLayout {anchors.fill:parent
                        TextField{id:titleField;implicitHeight:30;Layout.preferredHeight:30;objectName:"creativeAgreementTitle";placeholderText:"剧本名称";Layout.fillWidth:true}
                        TextField{id:languageField;implicitHeight:30;Layout.preferredHeight:30;objectName:"creativeAgreementLanguage";placeholderText:"语言，如 zh-CN";Layout.fillWidth:true}
                        Label{visible:editor.showLatest;text:"最新："+(editor.latest.title||"")+" / "+(editor.latest.language||"");wrapMode:Text.Wrap;Layout.fillWidth:true}
                        Button{visible:editor.showLatest;text:"采用最新基本信息";onClicked:{titleField.text=editor.latest.title;languageField.text=editor.latest.language}}
                    }
                }
                Repeater {
                    id:fields;model:editor.original.fields||[]
                    GroupBox {
                        id:field
                        required property var modelData
                        readonly property string key:modelData.id
                        readonly property string projectText:projectInput.text
                        readonly property string overrideText:scriptInput.text
                        readonly property bool inherits:inheritChoice.checked
                        readonly property string effectiveText:inherits?projectInput.text:scriptInput.text
                        title:modelData.label;Layout.fillWidth:true
                        ColumnLayout {anchors.fill:parent
                            CheckBox{id:inheritChoice;objectName:"creativeInherit_"+field.key;visible:editor.scriptScope&&!preview.checked;text:"继承工程默认";checked:!Object.prototype.hasOwnProperty.call(editor.original.script_overrides||{},field.key)||editor.original.script_overrides[field.key]===null}
                            Label {objectName:"creativeStatus_"+field.key;text:!editor.scriptScope&&!preview.checked?"工程默认：供未覆盖此项的剧本继承":field.inherits?"来源：工程默认":field.overrideText===""?"来源：当前剧本覆盖（明确留空）":"来源：当前剧本覆盖";visible:!!editor.screenplayId;wrapMode:Text.Wrap;Layout.fillWidth:true;color:host.t.muted}
                            TextArea{id:projectInput;objectName:"creativeProject_"+field.key;text:(editor.original.project_defaults||{})[field.key]||"";visible:!editor.scriptScope&&!preview.checked;placeholderText:"可留空，不强制填写";selectByMouse:true;wrapMode:TextEdit.Wrap;Layout.fillWidth:true;Layout.preferredHeight:90}
                            TextArea{id:scriptInput;objectName:"creativeOverride_"+field.key;text:(editor.original.script_overrides||{})[field.key]===undefined||(editor.original.script_overrides||{})[field.key]===null?(editor.original.project_defaults||{})[field.key]||"":editor.original.script_overrides[field.key];visible:editor.scriptScope&&!preview.checked;enabled:!inheritChoice.checked;placeholderText:"取消继承后可自定内容，清空表示本剧本明确无此约定";selectByMouse:true;wrapMode:TextEdit.Wrap;Layout.fillWidth:true;Layout.preferredHeight:90}
                            Label {objectName:"creativeEffective_"+field.key;visible:preview.checked||editor.scriptScope;text:"有效值："+(field.effectiveText||"（留空，无额外约定）");wrapMode:Text.Wrap;Layout.fillWidth:true}
                            Label {visible:editor.showLatest;text:"最新有效值："+((editor.latest.effective||{})[field.key]||"（留空）");wrapMode:Text.Wrap;Layout.fillWidth:true;color:host.t.muted}
                            Button {visible:editor.showLatest&&!preview.checked;text:"此项采用最新值";onClicked:{if(editor.scriptScope){var overrides=editor.latest.script_overrides||{};inheritChoice.checked=!Object.prototype.hasOwnProperty.call(overrides,field.key);scriptInput.text=overrides[field.key]||""}else projectInput.text=(editor.latest.project_defaults||{})[field.key]||""}}
                        }
                    }
                }
            }
        }
        RowLayout {
            Button {objectName:"creativeAgreementRefresh";text:"读取最新，保留草稿";enabled:editor.loaded&&!editor.busy;onClicked:editor.read(true)}
            Item {Layout.fillWidth:true}
            Button {text:"关闭";enabled:!editor.busy;onClicked:editor.close()}
            Button {objectName:"creativeAgreementSave";text:editor.busy?"处理中…":"保存创作约定";enabled:editor.loaded&&!editor.busy&&editor.validScope&&(!editor.screenplayId||(!!titleField.text.trim()&&!!languageField.text.trim()));onClicked:editor.save()}
        }
    }
}
