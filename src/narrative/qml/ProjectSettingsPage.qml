import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: page
    objectName: "projectSettingsPage"
    property var app
    readonly property var t: app.theme
    property var profile: ({})
    property var draft: ({})
    property var catalog: []
    property bool loaded: false
    property string granularity: "fine"
    property string message: ""
    property string projectNameDraft: ""
    property string boundProjectPath: ""
    property var promptSettings: ({prompts:[],revision:0})
    property bool promptsLoaded: false
    property bool promptSaving: false
    property string promptMessage: ""
    readonly property var selectedPrompt: (promptSettings.prompts || [])[promptType.currentIndex] || ({})
    function openPrompt(preview) {
        promptEditor.preview = preview
        promptEditor.promptId = selectedPrompt.id || ""
        promptEditor.promptLabel = selectedPrompt.label || "提示词"
        promptEditor.projectPath = app.project.path || ""
        promptEditor.revision = promptSettings.revision
        promptText.text = selectedPrompt.effective || ""
        promptEditor.originalText = promptText.text
        promptEditor.open()
    }
    function restorePrompt() {
        promptSaving = true
        bridge.savePromptOverride(selectedPrompt.id, null, promptSettings.revision)
    }
    readonly property var fields: [
        {key:"base_url", label:"API地址", name:"projectBaseUrl"},
        {key:"model", label:"模型", name:"projectModel"},
        {key:"key_env", label:"密钥环境变量名", name:"projectKeyEnv"},
        {key:"reasoning_effort", label:"推理等级（默认low）", name:"projectReasoning"},
        {key:"token_parameter", label:"输出参数名", name:"projectTokenParameter"},
        {key:"max_tokens", label:"最大输出tokens", name:"projectMaxTokens", type:"int"},
        {key:"context_window", label:"上下文窗口", name:"projectContextWindow", type:"int"},
        {key:"output_reserve", label:"输出预留", name:"projectOutputReserve", type:"int"},
        {key:"compaction_reserve", label:"压缩预留", name:"projectCompactionReserve", type:"int"},
        {key:"compaction_max_tokens", label:"压缩最大输出", name:"projectCompactionMax", type:"int"},
        {key:"timeout_seconds", label:"响应空闲超时（秒，非任务时限）", name:"projectIdleTimeout", type:"int"},
        {key:"temperature", label:"温度（空为服务默认）", name:"projectTemperature", type:"number"}
    ]
    function load() {
        if (app.mode === "workspace") {
            var path = app.project.path || ""
            if (path !== boundProjectPath) {
                boundProjectPath = path
                projectNameDraft = app.project.title || ""
            }
            bridge.loadProjectSettings()
            bridge.loadPromptSettings()
        }
    }
    function applyProfile(value) {
        profile = value || ({})
        var copy = ({})
        for (var key in profile) copy[key] = profile[key]
        draft = copy
        for (var i=0; i<catalog.length; ++i) if (catalog[i].id === profile.provider) providerBox.currentIndex=i
    }
    function collect() {
        var result = ({})
        for (var key in draft) result[key] = draft[key]
        for (var i=0; i<fields.length; ++i) {
            var field=fields[i], value=result[field.key]
            if (field.type === "int") result[field.key] = value === "" || value === null ? null : Number(value)
            else if (field.type === "number") result[field.key] = value === "" || value === null ? null : Number(value)
        }
        if (result.reasoning_effort === "") result.reasoning_effort = null
        return {extraction_granularity: granularity, profile:result}
    }
    Component.onCompleted: page.load()
    onVisibleChanged: if (visible) page.load()
    Connections {
        target: app
        function onProjectChanged() {
            var path = app.project.path || ""
            if (path !== page.boundProjectPath) {
                page.boundProjectPath = path
                page.projectNameDraft = app.project.title || ""
                page.loaded = false
                page.promptsLoaded = false; page.promptSettings = ({prompts:[],revision:0}); page.promptMessage = ""; page.promptSaving = false
                promptEditor.close()
                page.load()
            } else {
                page.projectNameDraft = app.project.title || ""
            }
        }
    }
    Connections {
        target: bridge
        function onResultReady(kind,payload) {
            if (kind === "prompt_settings") {
                if (payload.project_path !== (app.project.path || "")) return
                var priorId = page.selectedPrompt.id || ""
                page.promptSettings = payload; page.promptsLoaded = true; page.promptSaving = false
                for (var index=0;index<(payload.prompts || []).length;++index) if(payload.prompts[index].id===priorId)promptType.currentIndex=index
                if(payload.saved){page.promptMessage=payload.restored?"已恢复所选提示词默认内容。":"提示词已保存，下次任务生效。";promptEditor.close()}
            } else if (kind === "project_settings") {
                page.catalog=payload.catalog || page.catalog
                page.granularity=payload.extraction_granularity
                granularityBox.currentIndex=page.granularity === "fine" ? 1 : 0
                page.applyProfile(payload.profile)
                page.loaded=true
                page.message=payload.saved ? "工程设置已保存，仅影响本工程；新任务使用此设置。" : ""
            } else if (kind === "project_renamed") {
                page.projectNameDraft = payload.project.title || ""
                page.message = "工程名称已保存。"
            } else if (kind === "provider" && payload.profile) page.applyProfile(payload.profile)
        }
        function onErrorOccurred(kind,message) {
            if (kind === "prompt_settings") {page.promptSaving=false;page.promptMessage="提示词未保存："+message}
            else if (kind === "project_settings") page.message="设置未保存："+message
            else if (kind === "project_renamed") page.message="工程名称未保存："+message
        }
    }
    ScrollView {
        anchors.fill: parent
        anchors.margins: 20
        contentWidth: availableWidth
        ColumnLayout {
            width: parent.width
            spacing: 12
            Label { text:"工程设置"; font.pixelSize:18; font.weight:Font.DemiBold; color:page.t.ink }
            Label { text:"提取粒度与模型配置仅用于当前工程，不修改全局设置。"; wrapMode:Text.Wrap; Layout.fillWidth:true; color:page.t.muted }
            RowLayout {
                Layout.fillWidth:true
                Label { text:"工程名称"; color:page.t.ink; Layout.preferredWidth:200 }
                TextField {
                    objectName:"projectName"
                    Layout.fillWidth:true
                    enabled:page.loaded && !app.busy
                    text:page.projectNameDraft
                    onTextEdited: page.projectNameDraft=text
                    onAccepted: if (page.projectNameDraft.trim().length > 0) bridge.renameProject(page.projectNameDraft.trim())
                }
                Button {
                    objectName:"projectNameSave"
                    text:"保存名称"
                    enabled:page.loaded && !app.busy && page.projectNameDraft.trim().length > 0
                    onClicked: bridge.renameProject(page.projectNameDraft.trim())
                }
            }
            GroupBox {
                title:"内置提示词"
                Layout.fillWidth:true
                contentItem:ColumnLayout {
                    Label {Layout.fillWidth:true;wrapMode:Text.Wrap;color:page.t.muted;text:"按用途编辑当前工程的提示词。修改从下次任务生效；恢复默认只影响所选项。"}
                    ComboBox {id:promptType;objectName:"projectPromptType";Layout.fillWidth:true;model:page.promptSettings.prompts || [];textRole:"label";enabled:page.promptsLoaded&&!page.promptSaving}
                    Label {objectName:"projectPromptDescription";Layout.fillWidth:true;wrapMode:Text.Wrap;color:page.t.body;text:page.selectedPrompt.description || ""}
                    Label {objectName:"projectPromptState";text:page.selectedPrompt.overridden?"当前工程已自定义":"使用默认内容";color:page.t.muted}
                    Flow {
                        Layout.fillWidth:true;spacing:6
                        Button {objectName:"projectPromptEdit";text:"编辑提示词";enabled:page.promptsLoaded&&!!page.selectedPrompt.id&&!page.promptSaving&&!app.busy;onClicked:page.openPrompt(false)}
                        Button {objectName:"projectPromptPreview";text:"预览有效内容";enabled:page.promptsLoaded&&!!page.selectedPrompt.id&&!page.promptSaving;onClicked:page.openPrompt(true)}
                        Button {objectName:"projectPromptRestore";text:"恢复默认";enabled:page.selectedPrompt.overridden===true&&!page.promptSaving&&!app.busy;onClicked:page.restorePrompt()}
                    }
                    Label {objectName:"projectPromptMessage";Layout.fillWidth:true;wrapMode:Text.Wrap;visible:text.length>0;text:page.promptMessage;color:page.t.muted}
                }
            }
            RowLayout {
                Label { text:"提取粒度"; color:page.t.ink }
                ComboBox {
                    id:granularityBox; objectName:"projectGranularity"
                    model:["标准", "精细"]
                    enabled:page.loaded && !app.busy
                    onActivated: page.granularity=currentIndex === 1 ? "fine" : "standard"
                }
            }
            Label { text:"标准：适合阅读梳理；精细：面向改编，拆分更多情节事件。两档均默认不提取动作；动作仅在事件编辑器中按需提取。"; wrapMode:Text.Wrap; Layout.fillWidth:true; color:page.t.muted }
            RowLayout {
                Label { text:"模型服务"; color:page.t.ink }
                ComboBox {
                    id:providerBox; objectName:"projectProvider"
                    model:page.catalog; textRole:"name"; Layout.fillWidth:true
                    enabled:page.loaded && !app.busy
                    onActivated: if (currentIndex>=0 && currentIndex<page.catalog.length) bridge.loadProviderProfileFor(page.catalog[currentIndex].id)
                }
            }
            Repeater {
                model:page.fields
                RowLayout {
                    required property var modelData
                    Layout.fillWidth:true
                    Label { text:modelData.label; Layout.preferredWidth:200; color:page.t.ink; wrapMode:Text.Wrap }
                    TextField {
                        objectName:modelData.name
                        Layout.fillWidth:true
                        enabled:page.loaded && !app.busy
                        text:page.profile[modelData.key] === null || page.profile[modelData.key] === undefined ? "" : String(page.profile[modelData.key])
                        onTextEdited: page.draft[modelData.key]=text.trim()
                    }
                }
            }
            Label { objectName:"projectSettingsMessage"; text:page.message; visible:text.length>0; wrapMode:Text.Wrap; Layout.fillWidth:true; color:page.t.muted }
            RowLayout {
                Button { objectName:"projectSettingsSave"; text:"保存工程设置"; enabled:page.loaded && !app.busy; onClicked:bridge.saveProjectSettings(page.collect()) }
                Button { text:"重新载入"; enabled:!app.busy; onClicked:page.load() }
            }
        }
    }
    Dialog {
        id:promptEditor;objectName:"projectPromptEditor";modal:true;anchors.centerIn:parent;width:Math.min(900,page.width-40);height:Math.min(650,page.height-30)
        property bool preview:false
        property string promptId:""
        property string promptLabel:""
        property string projectPath:""
        property int revision:0
        property string originalText:""
        title:(preview?"有效内容 · ":"编辑 · ")+promptLabel
        contentItem:ColumnLayout {
            Label {Layout.fillWidth:true;wrapMode:Text.Wrap;color:page.t.muted;text:promptEditor.preview?"这是当前工程实际使用的内容。":"保存后从下次任务生效；提示词仅用于当前工程。"}
            ScrollView {Layout.fillWidth:true;Layout.fillHeight:true;TextArea{id:promptText;objectName:"projectPromptText";readOnly:promptEditor.preview;selectByMouse:true;wrapMode:TextEdit.Wrap}}
            RowLayout {
                Layout.fillWidth:true;Item{Layout.fillWidth:true}
                Button {text:promptEditor.preview?"关闭":"取消";onClicked:promptEditor.close()}
                Button {objectName:"projectPromptSave";text:"保存提示词";visible:!promptEditor.preview;enabled:page.promptsLoaded&&promptText.text!==promptEditor.originalText&&!page.promptSaving&&!app.busy;onClicked:{if(promptEditor.projectPath!==(app.project.path||"")){promptEditor.close();return}page.promptSaving=true;bridge.savePromptOverride(promptEditor.promptId,promptText.text,promptEditor.revision)}}
            }
        }
    }

}
