import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs

Rectangle {
    id: page
    SubmissionState{id:contentSubmission;owner:page;enabled:page.editing;operation:"save_asset";target:page.card.id?({"payload.id":page.card.id}):({"payload.category":page.category,"payload.domain":"adaptation"});onRestoreRequested:function(a){page.edit(a.payload)}}

    objectName: "assetsPage"
    property var app
    readonly property var t: app.theme
    color: t.paper
    property string category: "appearance"
    property string moment: ""
    property string nodeId: ""
    property var payload: ({assets: [], nodes: [], moments: [], revision: 0})
    property var card: ({})
    property var images: []
    property var references: []
    property bool editing: false
    readonly property var categoryKeys: ["appearance", "item", "location", "material"]
    readonly property var relevantNodes: (payload.nodes || []).filter(function(n) {
        if (n.suppressed || n.node_type !== (page.category === "appearance" ? "person" : page.category)) return false
        if (!page.moment || !n.point_id) return true
        var moments = page.payload.moments || []
        var current = moments.find(function(m) { return m.id === page.moment })
        var start = moments.find(function(m) { return m.id === n.point_id })
        return !current || !start || start.sort_id <= current.sort_id
    })
    function reload() { bridge.loadAssets(category, category === "material" ? "" : moment, category === "material" ? "" : nodeId) }
    function edit(c) {
        if(!contentSubmission.restoring)contentSubmission.begin()
        card = c; titleEdit.text = c.title || ""; detailEdit.text = c.detail || ""; promptEdit.text = c.prompt || ""
        images = c.images || []; references = c.reference_images || []
        var rows = [{id: "", name: "不限"}].concat(payload.moments || [])
        fromChoice.currentIndex = Math.max(0, rows.findIndex(function(n) { return n.id === (c.valid_from || "") }))
        toChoice.currentIndex = Math.max(0, rows.findIndex(function(n) { return n.id === (c.valid_to || "") }))
        editing = true
    }
    function imageUrl(path) { return path.indexOf("file:") === 0 ? path : "file:///" + path.replace(/\\/g, "/") }
    onVisibleChanged: if (visible && app.mode === "workspace") reload()
    Connections {
        target: bridge
        function onResultReady(kind, result) {
            if (kind === "project" || kind === "project_closed") { payload = ({assets: [], nodes: [], moments: [], revision: 0}); card = ({}); editing = false; nodeId = ""; moment = "" }
            if (kind === "assets" && result.category === page.category && (result.node_id || "") === page.nodeId && (result.moment || "") === page.moment) payload = result
            if (kind === "asset_saved") { editing = false; reload() }
            if (kind === "asset_detail" && result.asset.category === page.category) page.edit(result.asset)
        }
    }
    FileDialog {
        id: imagePicker
        property bool reference: false
        title: reference ? "选择参考图" : "选择设定图"
        nameFilters: ["图像 (*.png *.jpg *.jpeg *.webp *.gif)"]
        onAccepted: {
            var item = {path: selectedFile.toString(), caption: ""}
            if (reference) page.references = page.references.concat([item])
            else page.images = page.images.concat([item])
        }
    }
    ColumnLayout {
        SubmissionFeedback{state:contentSubmission}

        anchors.fill: parent; anchors.margins: 20; spacing: 12
        Label { text: "资产"; font.pixelSize: 22; font.bold: true; color: t.ink }
        TabBar {
            Layout.fillWidth: true
            onCurrentIndexChanged: {
                page.category = page.categoryKeys[currentIndex]; page.nodeId = ""; page.moment = ""; page.editing = false
                assetMomentChoice.currentIndex = 0; assetNodeChoice.currentIndex = 0
                if (page.visible && app.mode === "workspace") page.reload()
            }
            TabButton { text: "形象" }
            TabButton { text: "物品" }
            TabButton { text: "地点" }
            TabButton { text: "材质" }
        }
        RowLayout {
            visible: page.category !== "material"; Layout.fillWidth: true
            Label { text: "时间点"; color: t.muted }
            AssetMomentSelector {
                id: assetMomentChoice
                objectName: "assetMomentPicker"; Layout.fillWidth: true
                moments: page.payload.moments || []; emptyLabel: "当前有效"
                onActivated: { page.moment = model[currentIndex].id; page.editing = false; page.reload() }
            }
            Label { text: page.category === "appearance" ? "角色" : "节点"; color: t.muted }
            ComboBox {
                id: assetNodeChoice
                objectName: "assetNodePicker"; Layout.fillWidth: true
                model: [{id: "", name: "请选择"}].concat(page.relevantNodes); textRole: "name"
                onActivated: { page.nodeId = model[currentIndex].id; page.editing = false; page.reload() }
            }
        }
        RowLayout {
            Layout.fillWidth: true
            Label { text: page.category === "appearance" ? "选择角色后管理其全部形象设定" : "视觉设定资料卡"; color: t.muted }
            Item { Layout.fillWidth: true }
            Button { objectName: "assetAdd"; text: "新增设定"; enabled: page.category === "material" || !!page.nodeId; onClicked: page.edit({node_id: page.nodeId}) }
        }
        Repeater {
            model: page.payload.diagnostics || []
            delegate: RowLayout {
                required property var modelData
                Layout.fillWidth: true
                Label { text: modelData.message; color: t.warn; wrapMode: Text.Wrap; Layout.fillWidth: true }
                Button { text: "修正设定"; onClicked: bridge.loadAsset(modelData.asset_id) }
            }
        }
        SplitView {
            Layout.fillWidth: true; Layout.fillHeight: true
            ListView {
                objectName: "assetCards"; SplitView.preferredWidth: 250; clip: true; model: page.payload.assets || []
                delegate: ItemDelegate {
                    required property var modelData
                    width: ListView.view.width; text: modelData.title
                    onClicked: page.edit(modelData)
                }
            }
            ScrollView {
                SplitView.fillWidth: true; clip: true
                ColumnLayout {
                    width: parent.width; visible: page.editing; spacing: 12
                    Label { text: page.category === "appearance" ? "形象设定" : "视觉设定"; font.pixelSize: 18; color: t.ink }
                    TextField { id: titleEdit; objectName: "assetTitle"; Layout.fillWidth: true; placeholderText: "设定名称" }
                    TextArea { id: detailEdit; Layout.fillWidth: true; Layout.preferredHeight: 130; placeholderText: "详细设定"; wrapMode: TextEdit.Wrap; background: Rectangle { color: t.card; border.color: t.line; radius: t.radius } }
                    RowLayout {
                        visible: page.category !== "material"; Layout.fillWidth: true
                        Label { text: "生效"; color: t.muted }
                        AssetMomentSelector { id: fromChoice; Layout.fillWidth: true; moments: page.payload.moments || [] }
                        Label { text: "失效"; color: t.muted }
                        AssetMomentSelector { id: toChoice; Layout.fillWidth: true; moments: page.payload.moments || [] }
                    }
                    Label { text: "设定图"; color: t.ink }
                    Repeater {
                        model: page.images
                        delegate: RowLayout {
                            required property var modelData
                            required property int index
                            Image { source: page.imageUrl(modelData.path || ""); Layout.preferredWidth: 100; Layout.preferredHeight: 100; fillMode: Image.PreserveAspectFit }
                            TextField { Layout.fillWidth: true; text: modelData.caption || ""; placeholderText: "图片说明"; onTextEdited: { var rows = page.images.slice(); rows[index] = Object.assign({}, modelData, {caption: text}); page.images = rows } }
                            Button { text: "移除"; onClicked: { var rows = page.images.slice(); rows.splice(index, 1); page.images = rows } }
                        }
                    }
                    Button { text: "添加设定图"; onClicked: { imagePicker.reference = false; imagePicker.open() } }
                    Label { text: "参考图（可选）"; color: t.ink }
                    Repeater {
                        model: page.references
                        delegate: RowLayout {
                            required property var modelData
                            required property int index
                            Image { source: page.imageUrl(modelData.path || ""); Layout.preferredWidth: 100; Layout.preferredHeight: 100; fillMode: Image.PreserveAspectFit }
                            TextField { Layout.fillWidth: true; text: modelData.caption || ""; placeholderText: "参考说明"; onTextEdited: { var rows = page.references.slice(); rows[index] = Object.assign({}, modelData, {caption: text}); page.references = rows } }
                            Button { text: "移除"; onClicked: { var rows = page.references.slice(); rows.splice(index, 1); page.references = rows } }
                        }
                    }
                    Button { text: "添加参考图"; onClicked: { imagePicker.reference = true; imagePicker.open() } }
                    TextArea { id: promptEdit; Layout.fillWidth: true; Layout.preferredHeight: 90; placeholderText: "图像提示词（可选）"; wrapMode: TextEdit.Wrap; background: Rectangle { color: t.card; border.color: t.line; radius: t.radius } }
                    RowLayout {
                        Button { objectName: "assetSave"; text: "保存设定"; enabled: !!titleEdit.text.trim(); onClicked: {
                            var c = {category: page.category, node_id: page.category === "material" ? "" : (page.card.node_id || page.nodeId), domain: "adaptation", title: titleEdit.text, detail: detailEdit.text, prompt: promptEdit.text, images: page.images, reference_images: page.references, valid_from: page.category === "material" ? "" : fromChoice.model[fromChoice.currentIndex].id, valid_to: page.category === "material" ? "" : toChoice.model[toChoice.currentIndex].id}
                            if (page.card.id) c.id = page.card.id
                            bridge.saveAsset(c, page.payload.revision || 0)
                        } }
                        Button { text: "删除设定"; enabled: !!page.card.id; onClicked: bridge.deleteAsset(page.card.id, page.payload.revision || 0) }
                        Button { text: "取消"; onClicked: page.editing = false }
                    }
                }
            }
        }
    }
}
