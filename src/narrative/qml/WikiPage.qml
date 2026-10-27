import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts

// 角色 wiki：左边选角色，右边一页——
//   按当前阅读位置的状态、角色圣经、若干形象（每个形象可指定时间点，可挂五类图）。
// 圣经和形象是编辑素材，存在工程旁边的 _wiki 目录里；状态仍然来自属性记录。
Item {
    id: page
    objectName: "wikiPage"
    property var app
    readonly property var t: app.theme

    readonly property var scopeLabels: ({ world: "世界", cognition: "认知", claim: "声称" })

    property var characters: []
    property string selectedId: ""
    property var node: ({})
    property var stateRows: []
    property string cutName: ""
    property var wiki: ({ bible: "", appearances: [], kinds: [] })
    property string bibleDraft: ""
    property string pendingAppearanceId: ""
    property string pendingKind: ""
    property string editAppearanceId: ""

    ListModel { id: characterRows }

    function load() {
        bridge.loadWikiIndex()
    }

    function pick(id) {
        if (id === page.selectedId) return
        page.selectedId = id
        bridge.loadWiki(id)
    }

    function applyIndex(payload) {
        characterRows.clear()
        page.characters = payload.characters || []
        var known = false
        for (var i = 0; i < page.characters.length; ++i) {
            var row = page.characters[i]
            if (row.id === page.selectedId) known = true
            characterRows.append({
                id: row.id || "",
                labelId: row.label_id || row.id || "",
                name: row.name || "未命名",
                hasWiki: row.has_wiki === true
            })
        }
        // 换了工程时上一个人不在了：不清掉就会一直显示旧的选中项，还不会再发起读取。
        if (!known) page.selectedId = ""
        if (page.selectedId.length === 0 && characterRows.count > 0)
            page.pick(characterRows.get(0).id)
    }

    function applyWiki(payload) {
        page.node = payload.node || ({})
        page.stateRows = payload.state || []
        page.wiki = payload.wiki || ({ bible: "", appearances: [], kinds: [] })
        page.cutName = payload.cut_name || ""
        if (page.bibleDraft !== page.wiki.bible)
            page.bibleDraft = page.wiki.bible || ""
        // 列表里那个「有没有 wiki」的点跟着更新。
        for (var i = 0; i < characterRows.count; ++i) {
            if (characterRows.get(i).id === page.selectedId) {
                characterRows.setProperty(i, "hasWiki",
                    (page.wiki.bible || "").length > 0 || page.wiki.appearances.length > 0)
                break
            }
        }
    }

    function imageUrl(path) {
        return path ? "file:///" + String(path).replace(/\\/g, "/") : ""
    }

    function askImage(appearanceId, kind) {
        page.pendingAppearanceId = appearanceId
        page.pendingKind = kind
        imagePicker.open()
    }

    // 新增和编辑共用一张表单：编辑时把现值填进去，提交走 update。
    function openAppearanceDialog(appearance) {
        page.editAppearanceId = appearance ? (appearance.id || "") : ""
        appearanceName.text = appearance ? (appearance.name || "") : ""
        appearanceNote.text = appearance ? (appearance.note || "") : ""
        appearanceEvent.text = appearance ? (appearance.event_id || "") : ""
        appearanceDialog.open()
    }

    function submitAppearance() {
        if (page.editAppearanceId.length > 0)
            bridge.updateWikiAppearance(page.selectedId, page.editAppearanceId,
                appearanceName.text, appearanceNote.text, appearanceEvent.text)
        else
            bridge.addWikiAppearance(page.selectedId, appearanceName.text,
                appearanceNote.text, appearanceEvent.text)
        page.editAppearanceId = ""
        appearanceName.text = ""
        appearanceNote.text = ""
        appearanceEvent.text = ""
        appearanceDialog.close()
    }

    Component.onCompleted: if (app.mode === "workspace") page.load()

    Connections {
        target: app
        function onModeChanged() { if (app.mode === "workspace") page.load() }
    }

    Connections {
        target: bridge
        function onResultReady(kind, payload) {
            if (kind === "wiki_index") page.applyIndex(payload)
            else if (kind === "wiki") page.applyWiki(payload)
        }
    }

    FileDialog {
        id: imagePicker
        title: "选择图片"
        nameFilters: ["图片 (*.png *.jpg *.jpeg *.webp *.gif)"]
        onAccepted: {
            if (page.pendingAppearanceId.length > 0)
                bridge.addWikiImage(page.selectedId, page.pendingAppearanceId,
                    page.pendingKind, imagePicker.selectedFile.toString(), "")
        }
    }

    RowLayout {
        anchors.fill: parent
        anchors.margins: 12
        spacing: 12

        // -- 角色列表 ----------------------------------------------------
        Rectangle {
            Layout.preferredWidth: 220
            Layout.fillHeight: true
            color: t.panel
            radius: 8
            border.color: t.line

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 10
                spacing: 6

                Label {
                    text: "角色 " + characterRows.count
                    color: t.ink
                    font.pixelSize: 13
                    font.weight: Font.DemiBold
                }
                ListView {
                    objectName: "wikiCharacterList"
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    model: characterRows
                    spacing: 2
                    clip: true
                    delegate: Rectangle {
                        required property string id
                        required property string labelId
                        required property string name
                        required property bool hasWiki
                        width: ListView.view.width
                        height: 36
                        radius: 4
                        color: page.selectedId === id ? t.accentSoft : "transparent"
                        RowLayout {
                            anchors.fill: parent
                            anchors.leftMargin: 8
                            anchors.rightMargin: 8
                            spacing: 8
                            Label { text: labelId; color: t.muted; font.pixelSize: 11;
                                    Layout.preferredWidth: 40 }
                            Label { text: name; color: page.selectedId === id ? t.accent : t.ink
                                    font.pixelSize: 12; elide: Text.ElideRight
                                    Layout.fillWidth: true; Layout.minimumWidth: 0 }
                            Rectangle {
                                visible: hasWiki
                                width: 6; height: 6; radius: 3; color: t.ok
                            }
                        }
                        MouseArea { anchors.fill: parent; onClicked: page.pick(id) }
                    }
                    Label {
                        anchors.centerIn: parent
                        visible: characterRows.count === 0
                        text: "这个工程还没有人物节点。"
                        color: t.muted
                        font.pixelSize: 11
                    }
                }
            }
        }

        // -- 角色页 ------------------------------------------------------
        Rectangle {
            Layout.fillWidth: true
            Layout.fillHeight: true
            color: t.panel
            radius: 8
            border.color: t.line

            ScrollView {
                id: pageScroll
                anchors.fill: parent
                anchors.margins: 12
                clip: true

                ColumnLayout {
                    // 绑视口，不绑 parent：ScrollView 里的 parent 是内容项，宽度由内容撑开，
                    // 绑它就会缩成最宽那一行，右半边空着。
                    width: pageScroll.availableWidth
                    spacing: 10

                    Label {
                        Layout.fillWidth: true
                        text: (page.node.label_id || "") + "  " + (page.node.name || "（未选角色）")
                        color: t.ink
                        font.pixelSize: 16
                        font.weight: Font.DemiBold
                    }
                    Label {
                        Layout.fillWidth: true
                        visible: page.selectedId.length > 0
                        text: page.cutName.length > 0
                            ? "读到「" + page.cutName + "」时的状态" : "现在已知的状态"
                        color: t.muted
                        font.pixelSize: 11
                    }

                    // 状态：只读，一行一个方面。不是世界层面的说法就标出来，
                    // 认知和声称都不该看起来像已经发生的事。
                    Repeater {
                        model: page.stateRows
                        delegate: Label {
                            required property var modelData
                            readonly property string shown: modelData.value === null
                                || modelData.value === undefined || modelData.value === ""
                                ? "（空）" : String(modelData.value)
                            readonly property string scopeNote: modelData.scope
                                && modelData.scope !== "world"
                                ? "（" + (page.scopeLabels[modelData.scope] || modelData.scope) + "）"
                                : ""
                            Layout.fillWidth: true
                            text: modelData.label + "：" + shown + scopeNote
                                + (modelData.holder ? "　—— " + modelData.holder + " 的看法" : "")
                            color: t.body
                            font.pixelSize: 12
                            wrapMode: Text.Wrap
                        }
                    }
                    Label {
                        Layout.fillWidth: true
                        visible: page.selectedId.length > 0 && page.stateRows.length === 0
                        text: "这一刻还没有关于他的属性。"
                        color: t.muted
                        font.pixelSize: 11
                    }

                    Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: t.line }

                    Label {
                        Layout.fillWidth: true
                        text: "角色圣经"
                        color: t.ink
                        font.pixelSize: 13
                        font.weight: Font.DemiBold
                    }
                    Label {
                        Layout.fillWidth: true
                        text: "写这个人是谁：底色、动机、说话方式、不许写偏的底线。"
                        color: t.muted
                        font.pixelSize: 10
                    }
                    TextArea {
                        objectName: "wikiBible"
                        Layout.fillWidth: true
                        Layout.preferredHeight: 120
                        text: page.bibleDraft
                        wrapMode: TextArea.Wrap
                        placeholderText: "例如：嘴硬心软，遇事先笑再算账……"
                        onTextChanged: page.bibleDraft = text
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        Item { Layout.fillWidth: true }
                        Button {
                            objectName: "wikiBibleSave"
                            text: "保存圣经"
                            flat: true
                            enabled: page.selectedId.length > 0
                            onClicked: bridge.saveWikiBible(page.selectedId, page.bibleDraft)
                        }
                    }

                    Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: t.line }

                    RowLayout {
                        Layout.fillWidth: true
                        Label {
                            text: "形象 " + page.wiki.appearances.length
                            color: t.ink
                            font.pixelSize: 13
                            font.weight: Font.DemiBold
                        }
                        Item { Layout.fillWidth: true }
                        Button {
                            objectName: "wikiAddAppearance"
                            text: "新增形象"
                            flat: true
                            enabled: page.selectedId.length > 0
                            onClicked: page.openAppearanceDialog(null)
                        }
                    }

                    // 一个形象一块：设定 + 五类图。
                    Repeater {
                        model: page.wiki.appearances
                        delegate: Rectangle {
                            required property var modelData
                            // 形象本身：里面几层 Repeater 的 modelData 会被内层覆盖。
                            readonly property var appearance: modelData
                            Layout.fillWidth: true
                            implicitHeight: block.implicitHeight + 20
                            radius: 6
                            color: t.panelAlt
                            border.color: t.line

                            ColumnLayout {
                                id: block
                                anchors.fill: parent
                                anchors.margins: 10
                                spacing: 6

                                RowLayout {
                                    Layout.fillWidth: true
                                    Label {
                                        text: modelData.name
                                        color: t.ink
                                        font.pixelSize: 13
                                        font.weight: Font.DemiBold
                                    }
                                    Label {
                                        readonly property string when: modelData.event_name
                                            ? "从「" + modelData.event_name + "」起"
                                            : (modelData.event_id ? "从 E" + modelData.event_id + " 起"
                                                : "未指定时间点")
                                        text: when
                                        color: t.muted
                                        font.pixelSize: 10
                                    }
                                    Item { Layout.fillWidth: true }
                                    Label {
                                        text: "编辑"
                                        color: t.accent
                                        font.pixelSize: 10
                                        font.underline: true
                                        MouseArea {
                                            anchors.fill: parent
                                            cursorShape: Qt.PointingHandCursor
                                            onClicked: page.openAppearanceDialog(modelData)
                                        }
                                    }
                                    Label {
                                        text: "删除"
                                        color: t.bad
                                        font.pixelSize: 10
                                        font.underline: true
                                        MouseArea {
                                            anchors.fill: parent
                                            cursorShape: Qt.PointingHandCursor
                                            onClicked: bridge.removeWikiAppearance(
                                                page.selectedId, modelData.id)
                                        }
                                    }
                                }
                                Label {
                                    Layout.fillWidth: true
                                    visible: modelData.note.length > 0
                                    text: modelData.note
                                    color: t.body
                                    font.pixelSize: 11
                                    wrapMode: Text.Wrap
                                }

                                // 五类图：一行按钮 + 已挂的图。
                                Flow {
                                    Layout.fillWidth: true
                                    spacing: 6
                                    Repeater {
                                        model: page.wiki.kinds
                                        delegate: Button {
                                            required property var modelData
                                            text: "+ " + modelData.label
                                            flat: true
                                            font.pixelSize: 10
                                            onClicked: page.askImage(appearance.id, modelData.key)
                                        }
                                    }
                                }
                                Flow {
                                    Layout.fillWidth: true
                                    spacing: 8
                                    Repeater {
                                        model: appearance.images
                                        delegate: Column {
                                            required property var modelData
                                            spacing: 2
                                            Image {
                                                width: 132
                                                height: 132
                                                source: page.imageUrl(modelData.path)
                                                fillMode: Image.PreserveAspectFit
                                                asynchronous: true
                                            }
                                            Label {
                                                width: 132
                                                text: modelData.kind_label || modelData.kind
                                                color: t.muted
                                                font.pixelSize: 9
                                                elide: Text.ElideRight
                                            }
                                            Label {
                                                text: "移除"
                                                color: t.bad
                                                font.pixelSize: 9
                                                font.underline: true
                                                MouseArea {
                                                    anchors.fill: parent
                                                    cursorShape: Qt.PointingHandCursor
                                                    onClicked: bridge.removeWikiImage(
                                                        page.selectedId, appearance.id,
                                                        modelData.id)
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                    Label {
                        Layout.fillWidth: true
                        visible: page.wiki.appearances.length === 0
                        text: "还没有形象。一个形象可以指定从哪个事件起成立，并挂概念图、三视图、细节图、表情图、动作图。"
                        color: t.muted
                        font.pixelSize: 11
                        wrapMode: Text.Wrap
                    }
                    Item { Layout.preferredHeight: 8 }
                }
            }
        }
    }

    // 新增 / 编辑形象
    Dialog {
        id: appearanceDialog
        objectName: "wikiAppearanceDialog"
        title: page.editAppearanceId.length > 0 ? "编辑形象" : "新增形象"
        modal: true
        anchors.centerIn: parent
        width: 380

        contentItem: ColumnLayout {
            spacing: 6
            TextField {
                id: appearanceName
                objectName: "wikiAppearanceName"
                Layout.fillWidth: true
                placeholderText: "形象名，例如：青衫少年"
                onAccepted: page.submitAppearance()
            }
            TextField {
                id: appearanceNote
                objectName: "wikiAppearanceNote"
                Layout.fillWidth: true
                placeholderText: "设定：发型、衣饰、气质……"
                onAccepted: page.submitAppearance()
            }
            TextField {
                id: appearanceEvent
                objectName: "wikiAppearanceEvent"
                Layout.fillWidth: true
                placeholderText: "从哪个事件起成立（可空，例如 34）"
                onAccepted: page.submitAppearance()
            }
            RowLayout {
                Layout.fillWidth: true
                Item { Layout.fillWidth: true }
                Button { text: "取消"; flat: true; onClicked: appearanceDialog.close() }
                Button {
                    objectName: "wikiAppearanceSubmit"
                    text: page.editAppearanceId.length > 0 ? "保存" : "新增"
                    highlighted: true
                    onClicked: page.submitAppearance()
                }
            }
        }
    }
}
