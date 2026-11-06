import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Dialog {
    id: editor
    SubmissionState{id:contentSubmission;owner:editor;operation:"apply_script_block_changes";target:({unit_id:editor.scope.unit_id||null});onRestoreRequested:function(a){var c=(a.changes||[])[0]||{};editor.openFor(Object.assign({id:c.block_id},c.block||{}));editor.scope=Object.assign({},editor.scope,{unit_id:a.unit_id,body_revision:a.expected_body_revision,after_id:c.after_id||null})}}

    objectName: "scriptBlockEditor"
    property var host
    property var block: ({})
    property string errorText: ""
    property var scope: ({})
    property int requestId: 0
    property string saveToken: ""
    property bool saving: false
    property string discussionMessage: ""
    property bool pendingStart:false
    property var conversationTarget:({})
    property var savedValue:({})
    property bool pendingConversation:false
    property string pendingPrompt:""
    readonly property string currentKey:valueKey(currentValue())
    readonly property string savedKey:valueKey(savedValue)
    readonly property bool hasChanges:currentKey!==savedKey
    closePolicy:(saving||pendingStart)?Popup.NoAutoClose:Popup.CloseOnEscape
    readonly property var t: host.t
    title: block.id ? "修改" + (host.blockLabels[block.type] || "正文块") : "新增正文块"
    modal:true; width:Math.min(760,host.width-40); height:Math.min(740,host.height-30); anchors.centerIn:parent
    function openFor(value) {
        if(visible&&!contentSubmission.restoring)contentSubmission.begin();
        requestId++;saving=false;pendingStart=false;pendingConversation=false;pendingPrompt="";saveToken="";discussionMessage="";discussion.text=""
        scope={project_path:host.app.project.path||"",screenplay_id:host.screenplay.id,unit_id:host.unit.id,body_revision:host.unit.body_revision,after_id:host.lastBlockId()}
        block=value || ({}); var p=block.payload || {}; kind.currentIndex=Math.max(0,kind.values.indexOf(block.type || "action"))
        textField.text=p.text || ""; speaker.text=p.speaker_label || ""; speakerId.text=p.speaker_id || ""; parenthetical.text=p.parenthetical || ""
        delivery.currentIndex=Math.max(0,delivery.model.indexOf(p.delivery || "on_screen")); soundCategory.currentIndex=Math.max(0,soundCategory.model.indexOf(p.category || "effect")); screenStyle.currentIndex=Math.max(0,screenStyle.model.indexOf(p.presentation || "overlay")); transition.text=p.kind || "cut"; entityId.text=p.entity_id || ""; errorText=""; savedValue=currentValue();open()
    }
    function valueKey(value){function ordered(v){if(Array.isArray(v))return v.map(ordered);if(v&&typeof v==="object"){var out={};Object.keys(v).sort().forEach(function(k){out[k]=ordered(v[k])});return out}return v}return JSON.stringify(ordered(JSON.parse(JSON.stringify(value))))}
    function currentValue() {
        var type=kind.values[kind.currentIndex],p=type===block.type?JSON.parse(JSON.stringify(block.payload||{})):({})
        p.text=textField.text
        if(type==="dialogue"){p.speaker_id=speakerId.text.trim()||null;p.speaker_label=speaker.text;p.delivery=delivery.currentText;p.parenthetical=parenthetical.text}
        if(type==="screen_text")p.presentation=screenStyle.currentText
        if(type==="sound"){p.category=soundCategory.currentText;p.entity_id=entityId.text.trim()||null}
        if(type==="transition")p.kind=transition.text
        return {type:type,payload:p}
    }
    function save(sendAfter) {
        var value=currentValue(),type=value.type,p=value.payload
        pendingConversation=sendAfter===true;pendingPrompt=pendingConversation?discussion.text:""
        if(scope.project_path!==(host.app.project.path||"")){errorText="工程已切换，请重新打开正文块。";return}
        saving=true;errorText="";saveToken="block-editor-"+(host.structureOnly?"structure":"workspace")+"-"+requestId
        var change=block.id?{op:"update",block_id:block.id,block:{type:type,payload:p}}:{op:"insert",block:{type:type,payload:p},after_id:scope.after_id}
        host.act("apply_script_block_changes",{unit_id:scope.unit_id,changes:[change],target_block_ids:block.id?[block.id]:[],expected_body_revision:scope.body_revision,reason:"手工修改正文块",_view_request_token:saveToken})
    }
    function launchDiscussion(prompt) {
        conversationTarget={kind:"blocks",ids:[block.id],screenplay_id:scope.screenplay_id};pendingStart=true;errorText=""
        bridge.screenplayWritingTurn(prompt,conversationTarget)
    }
    function sendDiscussion() {
        if(!block.id||!discussion.text.trim()||scope.project_path!==(host.app.project.path||""))return
        if(hasChanges){save(true);return}
        launchDiscussion(discussion.text)
    }
    Connections {
        target:bridge
        function onResultReady(kind,payload) {
            if(kind==="screenplay_writing"&&editor.pendingStart&&editor.visible&&editor.valueKey(payload.target||{})===editor.valueKey(editor.conversationTarget)&&editor.scope.project_path===(editor.host.app.project.path||"")){
                editor.pendingStart=false;editor.host.app.assistantOpen=true;editor.close();return
            }
            if(kind!=="screenplay"||payload.operation!=="apply_script_block_changes"||!editor.visible||payload.request_token!==editor.saveToken||!editor.saveToken)return
            if(editor.scope.project_path!==(editor.host.app.project.path||""))return
            var saved=payload.result||{},updated=(saved.blocks||[]).find(function(b){return b.id===editor.block.id})
            if(updated)editor.block=JSON.parse(JSON.stringify(updated))
            editor.savedValue=editor.currentValue()
            if(saved.body_revision!==undefined)editor.scope=Object.assign({},editor.scope,{body_revision:saved.body_revision})
            editor.saveToken="";editor.saving=false
            if(editor.pendingConversation){editor.pendingConversation=false;editor.launchDiscussion(editor.pendingPrompt)}else editor.close()
        }
        function onErrorOccurred(kind,message){
            if(kind==="screenplay"&&editor.saving&&editor.visible){editor.saving=false;editor.pendingConversation=false;editor.errorText=message}
            if(kind==="screenplay_writing"&&editor.pendingStart&&editor.visible){editor.pendingStart=false;editor.errorText=message}
        }
    }
    contentItem:ColumnLayout {
        SubmissionFeedback{state:contentSubmission}

        enabled:!editor.saving&&!editor.pendingStart
        spacing:10
        Label { visible:!!editor.errorText; text:editor.errorText; color:t.bad; wrapMode:Text.Wrap; Layout.fillWidth:true }
        ScrollView {id:blockForm;contentWidth:availableWidth;Layout.fillWidth:true;Layout.fillHeight:true;clip:true
        ColumnLayout {width:blockForm.availableWidth;spacing:10
        ComboBox { id:kind; objectName:"scriptBlockType"; property var values:["action","scene_description","dialogue","screen_text","sound","transition"]; model:["动作","场景描写","对白","字幕","声音","转场"] }
        RowLayout { visible:kind.values[kind.currentIndex]==="dialogue"; Layout.fillWidth:true; TextField { id:speaker;objectName:"scriptBlockSpeaker"; placeholderText:"发声者名称"; Layout.fillWidth:true } TextField { id:speakerId;objectName:"scriptBlockSpeakerId"; placeholderText:"人物/群体ID（可选）"; Layout.fillWidth:true } }
        RowLayout { visible:kind.values[kind.currentIndex]==="dialogue"; Layout.fillWidth:true; ComboBox { id:delivery; model:["on_screen","off_screen","voice_over","inner_voice"] } TextField { id:parenthetical;objectName:"scriptBlockParenthetical"; placeholderText:"表演提示"; Layout.fillWidth:true } }
        RowLayout { visible:kind.values[kind.currentIndex]==="sound"; Layout.fillWidth:true; ComboBox { id:soundCategory; model:["effect","music","other"] } TextField { id:entityId; placeholderText:"声音实体ID（可选）"; Layout.fillWidth:true } }
        ComboBox { id:screenStyle; visible:kind.values[kind.currentIndex]==="screen_text"; model:["overlay","card"] }
        TextField { id:transition; visible:kind.values[kind.currentIndex]==="transition"; placeholderText:"转场类型，如fade_out"; Layout.fillWidth:true }
        ScrollView {contentWidth:availableWidth;Layout.fillWidth:true;Layout.preferredHeight:Math.max(120,editor.availableHeight-340);Layout.minimumHeight:120;clip:true
        TextArea { id:textField; objectName:"scriptBlockText"; placeholderText:"动作 / 场景描写 / 对白 / 字幕 / 声音 / 转场文字"; wrapMode:TextEdit.Wrap; clip:true }
        }
        Label {text:!block.id?"先保存新增正文块，再发送针对该块的修改意见。":editor.hasChanges?"发送意见时，先保存当前手工编辑，再让助手按意见修改。保存失败时保留输入，不发送对话。":"助手将依据当前已保存正文，按你的意见修改此块。";wrapMode:Text.Wrap;Layout.fillWidth:true;color:t.muted}
        TextArea {id:discussion;objectName:"scriptBlockDiscussion";placeholderText:"给助手的修改意见（可选）";enabled:!!editor.block.id;wrapMode:TextEdit.Wrap;Layout.fillWidth:true;Layout.preferredHeight:85}
        Label {visible:!!editor.discussionMessage;text:editor.discussionMessage;wrapMode:Text.Wrap;Layout.fillWidth:true;color:t.muted}
        }
        }
        RowLayout {
            Button {objectName:"scriptBlockDiscussionSend";text:editor.hasChanges?"保存并发送修改意见":"发送修改意见";enabled:!!editor.block.id&&!!discussion.text.trim()&&!editor.saving&&editor.scope.project_path===(host.app.project.path||"");onClicked:editor.sendDiscussion()}
            Item {Layout.fillWidth:true}
            Button {text:"关闭";enabled:!editor.saving;onClicked:editor.close()}
            Button {objectName:"scriptBlockSave";text:editor.saving?"保存中…":"保存手工修改";enabled:!editor.saving;onClicked:editor.save()}
        }
    }
}

