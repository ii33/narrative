import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

SplitView {
    id: script
    SubmissionState{id:contentSubmission;allowedActions:["update_shot"];authoredFields:["description", "shot_language", "sound_notes", "visual_elements"];identityKey:script.loadedId;owner:script;operation:"apply_storyboard_changes";target:({unit_id:script.desk.unitId||null});onRestoreRequested:function(a){var c=a.changes[0];script.loadedId=c.shot_id;script.loadedRevision=a.revision;description.text=c.description||"";language.text=c.shot_language||"";soundNotes.text=c.sound_notes||"";script.elements=c.visual_elements||[]}}

    objectName: "storyboardScriptView"
    required property var desk
    property string loadedId: ""
    property int loadedRevision: -1
    property var elements: []
    property var drafts: ({})
    property string savingId: ""
    function captureDraft(){if(loadedId)drafts[loadedId]={description:description.text,language:language.text,sound_notes:soundNotes.text,elements:JSON.parse(JSON.stringify(elements))}}
    function loadDraft() {
        if(savingId&&desk.board.revision!==loadedRevision){delete drafts[savingId];if(loadedId===savingId)loadedId="";savingId=""}
        captureDraft()
        loadedId=desk.shot.id||"";loadedRevision=desk.board.revision||0
        const draft=drafts[loadedId]
        description.text=draft?draft.description:desk.shot.description||"";language.text=draft?draft.language:desk.shot.shot_language||""
        soundNotes.text=draft?draft.sound_notes:desk.shot.sound_notes||""
        elements=JSON.parse(JSON.stringify(draft?draft.elements:desk.shot.visual_elements||[]))
    }
    function saveDraft() {
        if(loadedId!==desk.shot.id||loadedRevision!==desk.board.revision)return
        savingId=loadedId
        desk.save({op:"update_shot",shot_id:loadedId,description:description.text,shot_language:language.text,sound_notes:soundNotes.text,visual_elements:elements})
    }
    function pictureRows(owner){
        if((owner.panels||[]).length)return owner.panels.map((p,i)=>{const c=(p.candidates||[]).find(c=>c.id===p.adopted_candidate_id);const f=(owner.keyframes||[]).find(f=>f.id===p.frame_id);return {shot_id:owner.id,panel_id:p.id,label:"画格 "+(i+1),intent:p.intent||"",path:c?.path||p.base?.path||f?.sketch_path||"",snapshot:p.base?.kind==="snapshot"?p.base.png:""}})
        return (owner.keyframes||[]).filter(f=>!!f.sketch_path).map((f,i)=>({label:"关键帧 "+(i+1)+" · "+desk.seconds(f.offset_ms),intent:"",path:f.sketch_path,snapshot:""}))
    }
    function pictureSource(row){if(row.panel_id&&bridge)return bridge.storyboardPanelPreviewUrl(desk.board,row.shot_id,row.panel_id);return row.snapshot?"data:image/png;base64,"+row.snapshot:bridge&&row.path?bridge.storyboardImageUrl(row.path):""}
    Connections { target:desk; function onShotChanged(){Qt.callLater(script.loadDraft)} function onBoardChanged(){Qt.callLater(script.loadDraft)} function onUnitIdChanged(){drafts=({});loadedId=""} }
    Dialog {
        id:assetPicker;objectName:"storyboardVisualAssetPicker";title:"选择项目素材";modal:true;anchors.centerIn:parent;width:500;height:420
        property int elementIndex:-1
        property string category:""
        property var assets:[]
        function choose(index,kind){elementIndex=index;category=({person:"appearance",prop:"item",set:"location"}[kind]);assets=[];bridge.loadAssets(category,"","");open()}
        Connections {target:bridge;function onResultReady(kind,result){if(kind==="assets"&&assetPicker.opened&&result.category===assetPicker.category)assetPicker.assets=result.assets||[]}}
        contentItem:ListView {
            clip:true;model:assetPicker.assets
            delegate:ItemDelegate {
                required property var modelData
                width:ListView.view.width;height:70
                contentItem:RowLayout {
                    Image {source:bridge&&(modelData.images||[])[0]?.path?bridge.storyboardImageUrl(modelData.images[0].path):"";Layout.preferredWidth:80;Layout.preferredHeight:54;fillMode:Image.PreserveAspectFit}
                    Label {text:modelData.title+" · "+({appearance:"人物形象",item:"物品",location:"地点"}[assetPicker.category]);wrapMode:Text.Wrap;Layout.fillWidth:true}
                }
                onClicked:{script.elements[assetPicker.elementIndex].asset_id=modelData.id;script.elements=Array.from(script.elements);assetPicker.close()}
            }
        }
    }
    Dialog {
        id:intentEditor;objectName:"storyboardIntentDialog";title:"修改声音意图";modal:true;anchors.centerIn:parent;width:360
        property string intentId:""
        property int revision:0
        property string owner:""
        function edit(row){intentId=row.id;revision=desk.board.revision;owner=desk.unitId;editSpeaker.text=row.speaker||"";editText.text=row.text;editSource.currentIndex=Math.max(0,editSource.model.findIndex(b=>b.id===row.source_block_id));open()}
        contentItem:ColumnLayout {
        SubmissionFeedback{state:contentSubmission}

            ComboBox{id:editSource;Layout.fillWidth:true;textRole:"label";model:intentSource.model}
            TextField{id:editSpeaker;placeholderText:"说话人";Layout.fillWidth:true}
            TextArea{id:editText;wrapMode:TextEdit.Wrap;Layout.fillWidth:true}
            Button{text:"保存意图";enabled:!!editText.text.trim()&&!desk.busy;onClicked:{if(intentEditor.owner!==desk.unitId||intentEditor.revision!==desk.board.revision){intentEditor.close();return}desk.save({op:"update_sound_intent",intent_id:intentEditor.intentId,speaker:editSpeaker.text,text:editText.text,source_block_id:editSource.model[editSource.currentIndex].id});intentEditor.close()}}
        }
    }
    Connections {target:bridge;function onResultReady(kind,payload){if(kind==="storyboard"&&payload.error)script.savingId=""}function onErrorOccurred(kind,message){if(kind==="storyboard")script.savingId=""}}
    Component.onCompleted:loadDraft()
    ColumnLayout {
        SplitView.fillWidth:true;SplitView.minimumWidth:350
        RowLayout {
            Layout.fillWidth:true
            Label { text:"镜号 / 时长　　画面、镜头语言与声音";font.bold:true;Layout.fillWidth:true }
            Button { objectName:"storyboardScriptAddShot";text:"新增镜头";visible:!desk.scriptReadOnly;enabled:desk.ready&&!desk.busy&&!desk.approved;onClicked:desk.addScriptShot() }
        }
        ListView {
            id:rows;objectName:"storyboardScriptRows";Layout.fillWidth:true;Layout.fillHeight:true;clip:true;spacing:5
            model:desk.shots;currentIndex:desk.shotIndex
            onCurrentIndexChanged:if(currentIndex>=0)positionViewAtIndex(currentIndex,ListView.Contain)
            delegate:Rectangle {
                required property var modelData
                required property int index
                property string shotId:modelData.id
                objectName:"storyboardScriptRow"
                width:rows.width;height:Math.max(150,rowContent.implicitHeight+20)
                color:index===desk.shotIndex?"#e4ede8":"#f7f8f4";border.color:index===desk.shotIndex?"#587567":"#d5dcd6";radius:4
                TapHandler { onTapped:desk.seek(desk.startOf(index)) }
                ColumnLayout {
                    id:rowContent;anchors.fill:parent;anchors.margins:10
                    RowLayout {
                        Label { text:(index+1)+" · "+modelData.title+"　"+desk.seconds(modelData.duration_ms);font.bold:true;Layout.fillWidth:true;wrapMode:Text.Wrap }
                        Button { objectName:"storyboardScriptSelect";visible:!desk.scriptReadOnly;text:"定位";onClicked:desk.seek(desk.startOf(index)) }
                        Button { objectName:"storyboardScriptPanel";visible:!desk.scriptReadOnly;text:"画格";onClicked:{desk.seek(desk.startOf(index));desk.deskMode=1} }
                    }
                    Repeater {
                        model:script.pictureRows(modelData)
                        ColumnLayout {
                            required property var modelData
                            Layout.fillWidth:true
                            Label {text:modelData.label+(modelData.intent?" · "+modelData.intent:"");wrapMode:Text.Wrap;Layout.fillWidth:true}
                            Image {source:script.pictureSource(modelData);visible:!!source;Layout.fillWidth:true;Layout.preferredHeight:150;fillMode:Image.PreserveAspectFit}
                            Label {text:"待绘制画格";visible:!modelData.path&&!modelData.snapshot&&!modelData.panel_id;color:desk.t.muted;Layout.preferredHeight:50}
                        }
                    }
                    Label { text:modelData.description||"尚未填写画面说明";Layout.fillWidth:true;wrapMode:Text.Wrap;color:desk.t.ink }
                    Label { text:"镜头语言："+(modelData.shot_language||"未填写");Layout.fillWidth:true;wrapMode:Text.Wrap;color:desk.t.muted }
                    Label { text:(modelData.visual_elements||[]).map(e=>({person:"人物",prop:"道具",set:"场景"}[e.kind])+" · "+e.name+(e.usage?"："+e.usage:"")+(e.asset_id?"（已绑定素材）":"")).join("；");visible:!!text;Layout.fillWidth:true;wrapMode:Text.Wrap }
                    Label {visible:(modelData.source_block_ids||[]).length>0;text:"正文："+(desk.scene?.unit.blocks||[]).filter(b=>(modelData.source_block_ids||[]).includes(b.id)).map(b=>b.payload?.text||"").join("\n");Layout.fillWidth:true;wrapMode:Text.Wrap;color:desk.t.muted}
                    Label {text:modelData.sound_notes||"";visible:!!text;Layout.fillWidth:true;wrapMode:Text.Wrap}
                    Label { text:(modelData.sound_intent_ids||[]).map(id=>(desk.board.sound_intents||[]).find(s=>s.id===id)).filter(Boolean).map(s=>(s.speaker?s.speaker+"：":"")+s.text).join("\n");visible:!!text;Layout.fillWidth:true;wrapMode:Text.Wrap }
                }
            }
        }
    }
    ScrollView {
        visible:!desk.scriptReadOnly;SplitView.preferredWidth:350;SplitView.minimumWidth:260;clip:true;contentWidth:availableWidth
        ColumnLayout {
            width:parent.availableWidth;spacing:8
            Label { text:desk.shot.id?"第 "+(desk.shotIndex+1)+" 镜 · "+desk.shot.title:"选择或创建镜头";font.bold:true;wrapMode:Text.Wrap;Layout.fillWidth:true }
            RowLayout {
                Button { text:"镜头与正文引用";enabled:!!desk.shot.id&&!desk.busy&&!desk.approved;onClicked:desk.editScriptShot() }
                Button { objectName:"storyboardScriptImage";text:"生成 / 修改画面";enabled:!!desk.shot.id&&!desk.busy;onClicked:desk.editScriptFrame() }
            }
            Label { text:"画面说明" }
            TextArea { id:description;objectName:"storyboardScriptDescription";Layout.fillWidth:true;Layout.preferredHeight:110;wrapMode:TextEdit.Wrap;enabled:!!desk.shot.id&&!desk.busy&&!desk.approved;placeholderText:"人物动作、空间关系、构图与可见内容" }
            Label { text:"镜头语言" }
            TextArea { id:language;objectName:"storyboardScriptLanguage";Layout.fillWidth:true;Layout.preferredHeight:75;wrapMode:TextEdit.Wrap;enabled:description.enabled;placeholderText:"景别、角度、机位与运镜意图" }
            Label { text:"视觉元素与素材绑定";font.bold:true }
            Repeater {
                model:script.elements
                ColumnLayout {
                    required property var modelData
                    required property int index
                    Layout.fillWidth:true
                    RowLayout {
                        ComboBox { model:["人物","道具","场景"];currentIndex:["person","prop","set"].indexOf(modelData.kind);enabled:description.enabled;onActivated:script.elements[index].kind=["person","prop","set"][currentIndex] }
                        TextField { text:modelData.name||"";placeholderText:"名称";Layout.fillWidth:true;enabled:description.enabled;onTextEdited:script.elements[index].name=text }
                        Button { text:"移除";enabled:description.enabled;onClicked:script.elements=script.elements.filter((e,i)=>i!==index) }
                    }
                    TextField { text:modelData.usage||"";placeholderText:"本镜用途";Layout.fillWidth:true;enabled:description.enabled;onTextEdited:script.elements[index].usage=text }
                    RowLayout {
                        Label {text:modelData.asset_id?"已绑定项目素材":"尚未绑定素材";Layout.fillWidth:true}
                        Button {objectName:"storyboardChooseVisualAsset";text:"选择素材";enabled:description.enabled;onClicked:assetPicker.choose(index,modelData.kind)}
                        Button {text:"解除";enabled:description.enabled&&!!modelData.asset_id;onClicked:{script.elements[index].asset_id=null;script.elements=Array.from(script.elements)}}
                    }
                }
            }
            Button { objectName:"storyboardScriptAddElement";text:"添加视觉元素";enabled:description.enabled;onClicked:script.elements=script.elements.concat([{kind:"person",name:"",usage:"",asset_id:null}]) }
            Button { objectName:"storyboardScriptSave";text:"保存本镜文字与绑定";enabled:description.enabled&&script.loadedRevision===desk.board.revision;onClicked:script.saveDraft() }
            Label {text:"本镜声音使用说明"}
            TextArea {id:soundNotes;objectName:"storyboardScriptSoundNotes";enabled:description.enabled;Layout.fillWidth:true;wrapMode:TextEdit.Wrap;placeholderText:"可听性、画外、续音、入点与停顿"}
            Label { text:"声音意图在多镜之间共享；编排中的声音片段负责具体时间。";wrapMode:Text.Wrap;Layout.fillWidth:true;color:desk.t.muted }
            Repeater {
                model:desk.board.sound_intents||[]
                RowLayout {
                    required property var modelData
                    Layout.fillWidth:true
                    CheckBox { checked:(desk.shot.sound_intent_ids||[]).includes(modelData.id);enabled:description.enabled;onToggled:{var ids=Array.from(desk.shot.sound_intent_ids||[]);desk.save({op:"update_shot",shot_id:desk.shot.id,sound_intent_ids:checked?ids.concat([modelData.id]):ids.filter(id=>id!==modelData.id)})} }
                    Label { text:(modelData.speaker?modelData.speaker+"：":"")+modelData.text;Layout.fillWidth:true;wrapMode:Text.Wrap }
                    Button {text:"编辑";enabled:description.enabled;onClicked:intentEditor.edit(modelData)}
                    Button {text:"删除";enabled:description.enabled&&!desk.shots.some(s=>(s.sound_intent_ids||[]).includes(modelData.id));onClicked:desk.save({op:"delete_sound_intent",intent_id:modelData.id})}
                }
            }
            ComboBox {id:intentSource;objectName:"storyboardIntentSource";Layout.fillWidth:true;textRole:"label";model:[{id:null,label:"独立声音意图（不引用正文块）"}].concat((desk.scene?.unit.blocks||[]).filter(b=>["dialogue","sound"].includes(b.type)).map(b=>({id:b.id,label:(b.payload?.speaker_label||"")+" "+(b.payload?.text||b.type)})))}
            ComboBox { id:soundKind;model:["对白","音效","静默"];enabled:description.enabled }
            TextField { id:speaker;objectName:"storyboardIntentSpeaker";placeholderText:"说话人（对白）";enabled:description.enabled&&soundKind.currentIndex===0;Layout.fillWidth:true }
            TextArea { id:intentText;objectName:"storyboardIntentText";placeholderText:"声音内容或意图";enabled:description.enabled;Layout.fillWidth:true;wrapMode:TextEdit.Wrap }
            Button { objectName:"storyboardAddIntent";text:"新增共享声音意图";enabled:description.enabled&&!!intentText.text.trim();onClicked:desk.save({op:"create_sound_intent",kind:["dialogue","effect","silence"][soundKind.currentIndex],speaker:speaker.text,text:intentText.text,source_block_id:intentSource.model[intentSource.currentIndex].id}) }
        }
    }
}
