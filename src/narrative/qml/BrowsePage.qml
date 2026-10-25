import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// 数据浏览：左侧分类，中间节点列表（编号即阅读顺序），右侧详情 + 属性。
Item {
    id: page
    objectName: "browsePage"
    property var app
    readonly property var t: app.theme

    property var allNodes: []
    property string selectedType: ""
    property string query: ""
    property string selectedId: ""
    property var detail: ({})
    property var counts: ({})
    property int revision: 0
    property int total: 0

    readonly property var typeLabels: ({
        person: "人物", group: "群体", item: "物品",
        location: "地点", event: "事件", scene: "场景", storyline: "故事线", other: "其他"
    })
    readonly property var typeKeys: ["person", "group", "item", "location", "event", "scene", "storyline", "other"]

    // 属性名由模型给出（identity / height / state…），这里只补常见的中文说法。
    readonly property var propertyLabels: ({
        identity: "身份", state: "状态", status: "状况", holder: "持有者",
        height: "身高", age: "年龄", appearance: "外貌", attire: "衣着",
        clothing: "衣着", skin: "肤色", face: "面容", eyes: "眼睛",
        belief: "认知", knowledge: "知识", plan: "打算", location: "位置",
        color: "颜色", function: "功能", content: "内容", definition: "定义",
        nature: "性质", type: "类型", reputation: "名声", relation: "关系",
        quality: "品质", realm: "境界", cultivation: "修为", deed: "行径",
        injury: "伤势", effect: "效果", equipment: "装备", protection: "防护",
        spending: "花费", education: "学历", inscription: "题字",
        seclusion_duration: "闭关时长"
    })
    readonly property var scopeLabels: ({world: "世界", cognition: "认知", claim: "声称"})

    ListModel { id: rows }
    ListModel { id: categories }

    // Plain JS arrays, not ListModel: a record carries nested arrays (related
    // nodes) and ListModel drops those on the way in.
    property var propertyRows: []
    property var expandedProps: ({})

    function load() {
        bridge.loadGraphView({view: "table", limit: 2000})
    }

    function nameOf(id) {
        for (var i = 0; i < allNodes.length; ++i)
            if (allNodes[i].id === id) return allNodes[i].name
        return id
    }

    function applyNodes(list) {
        allNodes = list || []
        var tally = {}
        for (var k = 0; k < page.typeKeys.length; ++k)
            tally[page.typeKeys[k]] = 0
        for (var i = 0; i < allNodes.length; ++i) {
            var kind = allNodes[i].node_type || "other"
            tally[kind] = (tally[kind] || 0) + 1
        }
        counts = tally
        rebuildCategories()
        rebuild()
    }

    function rebuildCategories() {
        categories.clear()
        categories.append({key: "", label: "全部", count: allNodes.length})
        for (var i = 0; i < page.typeKeys.length; ++i) {
            var key = page.typeKeys[i]
            categories.append({key: key, label: page.typeLabels[key], count: counts[key] || 0})
        }
    }

    function matches(node) {
        if (page.selectedType.length > 0 && (node.node_type || "other") !== page.selectedType)
            return false
        var needle = page.query.trim().toLowerCase()
        if (needle.length === 0) return true
        if (String(node.name || "").toLowerCase().indexOf(needle) >= 0) return true
        var aliases = node.aliases || []
        for (var i = 0; i < aliases.length; ++i)
            if (String(aliases[i]).toLowerCase().indexOf(needle) >= 0) return true
        return false
    }

    function rebuild() {
        rows.clear()
        var shown = 0
        for (var i = 0; i < allNodes.length; ++i) {
            var node = allNodes[i]
            if (!page.matches(node)) continue
            shown += 1
            rows.append({
                id: node.id || "",
                name: node.name || "未命名",
                kindLabel: page.typeLabels[node.node_type] || node.node_type || "节点",
                note: node.identity_note || ""
            })
        }
        total = shown
    }

    function pickType(kind) {
        selectedType = kind
        rebuild()
    }

    function select(id) {
        selectedId = id
        detail = ({})
        propertyRows = []
        expandedProps = ({})
        bridge.nodeDetail(id)
        bridge.loadNodeProperties(id)
    }

    function applyProperties(payload) {
        var list = payload.properties || []
        var built = []
        for (var i = 0; i < list.length; ++i) {
            var item = list[i]
            var records = item.records || []
            built.push({
                key: item.property_id + "|" + (item.scope || "world") + "|" + (item.holder_id || ""),
                label: page.propertyLabels[item.property_id] || item.property_id || "",
                valueText: item.retracted ? "（已撤回）"
                    : item.value_node ? item.value_node.name
                    : (item.value === null || item.value === undefined ? "（空）" : String(item.value)),
                valueNodeId: item.value_node ? item.value_node.id : "",
                scopeText: page.scopeLabels[item.scope] || item.scope || "世界",
                holderId: item.holder_id || "",
                holderName: item.holder_name
                    || (item.holder_id ? page.nameOf(item.holder_id) : ""),
                validFrom: item.current_retracted ? "" : ("起于 " + (item.current_from_event || "—")),
                historyText: item.history_count > 0
                    ? "另有 " + item.history_count + " 条历史" : "",
                countText: records.length > 1 ? records.length + " 条披露" : "",
                records: records
            })
        }
        expandedProps = ({})
        propertyRows = built
    }

    function jumpTo(id) {
        if (!id) return
        // A node may be filtered out of the list; clear the filter first so the
        // jump always lands somewhere visible.
        if (page.selectedType.length > 0 || page.query.length > 0) {
            page.selectedType = ""
            page.query = ""
            searchField.text = ""
            page.rebuild()
        }
        page.select(id)
        for (var i = 0; i < rows.count; ++i) {
            if (rows.get(i).id === id) {
                nodeList.positionViewAtIndex(i, ListView.CenterInView)
                return
            }
        }
    }

    function toggleProperty(key) {
        var next = ({})
        for (var name in expandedProps)
            next[name] = expandedProps[name]
        next[key] = !next[key]
        expandedProps = next
    }

    // Loads only once a project is open; the launcher has no project yet.
    Component.onCompleted: if (app.mode === "workspace") page.load()

    Connections {
        target: app
        function onModeChanged() {
            if (app.mode === "workspace") {
                page.selectedId = ""
                page.detail = ({})
                page.selectedType = ""
                page.query = ""
                searchField.text = ""
                propertyRows = []
                expandedProps = ({})
                page.load()
            }
        }
    }

    Connections {
        target: bridge
        function onResultReady(kind, payload) {
            if (kind === "graph_view") {
                var canvas = payload.canvas || ({})
                revision = canvas.revision || revision
                page.applyNodes(canvas.nodes)
            } else if (kind === "detail") {
                detail = payload
                var index = payload.node ? page.typeKeys.indexOf(payload.node.node_type) : -1
                kindEditor.currentIndex = index >= 0 ? index : 0
            } else if (kind === "node_properties") {
                page.applyProperties(payload)
            } else if (kind === "node_edit") {
                app.toast("已提交修订，等待审核", false)
            }
        }
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 20
        spacing: 12

        RowLayout {
            Layout.fillWidth: true
            spacing: 8

            TextField {
                id: searchField
                objectName: "browseSearchField"
                Layout.preferredWidth: 300
                placeholderText: "搜索名称或别名"
                onTextChanged: { page.query = text; page.rebuild() }
            }
            Label {
                text: total + " 个节点 · 修订 " + revision
                color: t.muted
                font.pixelSize: 12
            }
            Item { Layout.fillWidth: true }
            Button {
                text: "刷新"
                flat: true
                onClicked: page.load()
            }
        }

        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 12

            // -- categories ---------------------------------------------
            Rectangle {
                Layout.preferredWidth: 190
                Layout.fillHeight: true
                color: t.card
                border.color: t.line
                radius: t.radius

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 10
                    spacing: 2

                    Label {
                        text: "分类"
                        color: t.muted
                        font.pixelSize: 11
                        Layout.bottomMargin: 4
                    }

                    Repeater {
                        model: categories

                        delegate: Item {
                            required property var modelData
                            Layout.fillWidth: true
                            Layout.preferredHeight: 34

                            Rectangle {
                                anchors.fill: parent
                                radius: 5
                                color: page.selectedType === modelData.key ? t.accentSoft : "transparent"
                            }
                            RowLayout {
                                anchors.fill: parent
                                anchors.leftMargin: 10
                                anchors.rightMargin: 10
                                spacing: 6
                                Label {
                                    text: modelData.label
                                    color: page.selectedType === modelData.key ? t.accent : t.ink
                                    font.pixelSize: 13
                                    Layout.fillWidth: true
                                }
                                Label {
                                    text: modelData.count
                                    color: modelData.count > 0 ? t.muted : t.line
                                    font.pixelSize: 11
                                }
                            }
                            MouseArea {
                                anchors.fill: parent
                                cursorShape: Qt.PointingHandCursor
                                onClicked: page.pickType(modelData.key)
                            }
                        }
                    }

                    Item { Layout.fillHeight: true }
                }
            }

            // -- node list ----------------------------------------------
            Rectangle {
                Layout.fillWidth: true
                Layout.fillHeight: true
                color: t.card
                border.color: t.line
                radius: t.radius

                ListView {
                    id: nodeList
                    anchors.fill: parent
                    anchors.margins: 8
                    clip: true
                    model: rows
                    spacing: 2
                    delegate: Rectangle {
                        required property string id
                        required property string name
                        required property string kindLabel

                        width: ListView.view.width
                        height: 42
                        radius: 4
                        color: page.selectedId === id ? t.accentSoft : "transparent"

                        RowLayout {
                            anchors.fill: parent
                            anchors.leftMargin: 10
                            anchors.rightMargin: 10
                            spacing: 10
                            Label {
                                text: id
                                color: t.muted
                                font.pixelSize: 11
                                Layout.preferredWidth: 48
                            }
                            Label {
                                text: kindLabel
                                color: t.accent
                                font.pixelSize: 11
                                Layout.preferredWidth: 48
                            }
                            Label {
                                text: name
                                color: page.selectedId === id ? t.accent : t.ink
                                font.pixelSize: 13
                                elide: Text.ElideRight
                                Layout.fillWidth: true
                            }
                        }
                        MouseArea {
                            anchors.fill: parent
                            onClicked: page.select(id)
                        }
                    }
                    Label {
                        anchors.centerIn: parent
                        visible: rows.count === 0
                        text: page.allNodes.length === 0 ? "这个工程还没有节点。" : "该分类下没有节点。"
                        color: t.muted
                    }
                }
            }

            // -- detail --------------------------------------------------
            Rectangle {
                Layout.preferredWidth: 440
                Layout.fillHeight: true
                color: t.card
                border.color: t.line
                radius: t.radius

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 16
                    spacing: 8

                    Label {
                        text: detail.node ? detail.node.name : "未选择节点"
                        font.pixelSize: 16
                        font.weight: Font.DemiBold
                        color: t.ink
                        wrapMode: Text.Wrap
                        Layout.fillWidth: true
                    }
                    Label {
                        Layout.fillWidth: true
                        text: detail.node
                            ? "编号 " + detail.node.id + " · "
                              + (page.typeLabels[detail.node.node_type] || detail.node.node_type)
                            : "在中间选择一个节点"
                        color: t.muted
                        font.pixelSize: 12
                    }

                    ScrollView {
                        id: detailScroll
                        objectName: "browseDetailScroll"
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true

                        ColumnLayout {
                            width: detailScroll.availableWidth
                            spacing: 8

                            Label {
                                Layout.fillWidth: true
                                wrapMode: Text.Wrap
                                visible: text.length > 0
                                text: detail.node && (detail.node.aliases || []).length > 0
                                    ? "别名：" + detail.node.aliases.join("、") : ""
                                color: t.muted
                                font.pixelSize: 12
                            }
                            Label {
                                Layout.fillWidth: true
                                wrapMode: Text.Wrap
                                visible: text.length > 0
                                text: detail.node && detail.node.identity_note ? detail.node.identity_note : ""
                                color: t.body
                                font.pixelSize: 13
                            }
                            Label {
                                Layout.fillWidth: true
                                wrapMode: Text.Wrap
                                visible: text.length > 0
                                text: detail.node && detail.node.evidence
                                    ? "原文：" + detail.node.evidence.quote : ""
                                color: t.muted
                                font.pixelSize: 12
                            }
                            Label {
                                Layout.fillWidth: true
                                visible: text.length > 0
                                text: detail.node && detail.node.disclosure
                                    ? "披露位置：" + detail.node.disclosure.start : ""
                                color: t.muted
                                font.pixelSize: 11
                            }

                            Rectangle {
                                Layout.fillWidth: true
                                Layout.preferredHeight: 1
                                Layout.topMargin: 4
                                color: t.line
                            }

                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 8
                                Label {
                                    text: "属性"
                                    color: t.ink
                                    font.pixelSize: 13
                                    font.weight: Font.DemiBold
                                }
                                Label {
                                    text: propertyRows.length + " 项"
                                    color: t.muted
                                    font.pixelSize: 11
                                }
                                Item { Layout.fillWidth: true }
                                Label {
                                    text: "点开看披露序列"
                                    color: t.muted
                                    font.pixelSize: 10
                                    visible: propertyRows.length > 0
                                }
                            }

                            Repeater {
                                model: propertyRows

                                delegate: Column {
                                    required property var modelData

                                    readonly property bool expanded: page.expandedProps[modelData.key] === true

                                    Layout.fillWidth: true
                                    spacing: 0

                                    Rectangle {
                                        width: parent.width
                                        height: 46
                                        radius: 4
                                        color: propHover.hovered ? t.accentSoft : "transparent"

                                        HoverHandler { id: propHover }

                                        // Declared first so the labels above it win the click;
                                        // the holder name is its own target.
                                        MouseArea {
                                            anchors.fill: parent
                                            cursorShape: Qt.PointingHandCursor
                                            onClicked: page.toggleProperty(modelData.key)
                                        }

                                        ColumnLayout {
                                            anchors.fill: parent
                                            anchors.leftMargin: 8
                                            anchors.rightMargin: 8
                                            anchors.topMargin: 4
                                            anchors.bottomMargin: 4
                                            spacing: 0

                                            RowLayout {
                                                Layout.fillWidth: true
                                                spacing: 8
                                                Label {
                                                    text: (expanded ? "− " : "+ ") + modelData.label
                                                    color: t.ink
                                                    font.pixelSize: 12
                                                    Layout.preferredWidth: 96
                                                    elide: Text.ElideRight
                                                }
                                                Label {
                                                    text: modelData.valueText
                                                    color: modelData.valueNodeId.length > 0 ? t.accent : t.body
                                                    font.pixelSize: 12
                                                    font.underline: modelData.valueNodeId.length > 0
                                                    elide: Text.ElideRight
                                                    Layout.fillWidth: true

                                                    MouseArea {
                                                        anchors.fill: parent
                                                        enabled: modelData.valueNodeId.length > 0
                                                        cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
                                                        onClicked: page.jumpTo(modelData.valueNodeId)
                                                    }
                                                }
                                                Label {
                                                    text: modelData.scopeText
                                                    color: t.accent
                                                    font.pixelSize: 10
                                                }
                                                Label {
                                                    visible: modelData.holderId.length > 0
                                                    text: modelData.holderName
                                                    color: t.accent
                                                    font.pixelSize: 10
                                                    font.underline: true
                                                    MouseArea {
                                                        anchors.fill: parent
                                                        cursorShape: Qt.PointingHandCursor
                                                        onClicked: page.jumpTo(modelData.holderId)
                                                    }
                                                }
                                            }

                                            Label {
                                                Layout.fillWidth: true
                                                visible: modelData.validFrom.length > 0
                                                text: modelData.validFrom
                                                    + (modelData.historyText.length > 0
                                                        ? " · " + modelData.historyText : "")
                                                color: t.muted
                                                font.pixelSize: 10
                                                elide: Text.ElideRight
                                            }
                                        }
                                    }

                                    Repeater {
                                        model: expanded ? modelData.records : []

                                        delegate: Column {
                                            required property var modelData

                                            width: parent.width
                                            leftPadding: 18
                                            topPadding: 2
                                            bottomPadding: 6
                                            spacing: 2

                                            RowLayout {
                                                width: parent.width - parent.leftPadding
                                                spacing: 6

                                                Label {
                                                    text: modelData.kind === "property_retraction"
                                                        ? "撤回"
                                                        : modelData.is_current ? "当前" : "历史"
                                                    color: modelData.is_current ? t.ok : t.muted
                                                    font.pixelSize: 10
                                                    Layout.preferredWidth: 28
                                                }
                                                Label {
                                                    Layout.fillWidth: true
                                                    wrapMode: Text.Wrap
                                                    text: modelData.kind === "property_retraction"
                                                        ? "该说法已撤回"
                                                        : modelData.value_node ? modelData.value_node.name
                                                        : String(modelData.value === null
                                                            || modelData.value === undefined
                                                            ? "（空）" : modelData.value)
                                                    color: modelData.value_node ? t.accent : t.body
                                                    font.pixelSize: 11
                                                    font.underline: !!modelData.value_node

                                                    MouseArea {
                                                        anchors.fill: parent
                                                        enabled: !!modelData.value_node
                                                        cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
                                                        onClicked: page.jumpTo(modelData.value_node.id)
                                                    }
                                                }
                                            }
                                            RowLayout {
                                                width: parent.width - parent.leftPadding
                                                spacing: 4

                                                Label {
                                                    text: "事件"
                                                    color: t.muted
                                                    font.pixelSize: 10
                                                }
                                                Label {
                                                    text: modelData.event_name
                                                        || modelData.event_id || "—"
                                                    color: modelData.event_name ? t.accent : t.muted
                                                    font.pixelSize: 10
                                                    font.underline: !!modelData.event_name
                                                    MouseArea {
                                                        anchors.fill: parent
                                                        cursorShape: Qt.PointingHandCursor
                                                        onClicked: page.jumpTo(modelData.event_id)
                                                    }
                                                }
                                                Label {
                                                    text: "→ " + (modelData.valid_to_event || "至今")
                                                    color: t.muted
                                                    font.pixelSize: 10
                                                    elide: Text.ElideRight
                                                }
                                                Label {
                                                    Layout.fillWidth: true
                                                    text: "· 位置 " + (modelData.disclosure
                                                        ? modelData.disclosure.start : "—")
                                                        + " · " + (modelData.source || "未标注来源")
                                                    color: t.muted
                                                    font.pixelSize: 10
                                                    elide: Text.ElideRight
                                                }
                                            }

                                            RowLayout {
                                                width: parent.width - parent.leftPadding
                                                spacing: 4
                                                visible: (modelData.related || []).length > 0

                                                Label {
                                                    text: "关联"
                                                    color: t.muted
                                                    font.pixelSize: 10
                                                }
                                                Repeater {
                                                    model: modelData.related || []

                                                    delegate: Label {
                                                        required property var modelData
                                                        text: modelData.name
                                                        color: t.accent
                                                        font.pixelSize: 10
                                                        font.underline: true
                                                        MouseArea {
                                                            anchors.fill: parent
                                                            cursorShape: Qt.PointingHandCursor
                                                            onClicked: page.jumpTo(modelData.id)
                                                        }
                                                    }
                                                }
                                                Item { Layout.fillWidth: true }
                                            }
                                            Label {
                                                width: parent.width - parent.leftPadding
                                                wrapMode: Text.Wrap
                                                visible: !!modelData.evidence
                                                text: modelData.evidence ? "「" + modelData.evidence.quote + "」" : ""
                                                color: t.muted
                                                font.pixelSize: 10
                                            }
                                        }
                                    }
                                }
                            }

                            Label {
                                Layout.fillWidth: true
                                Layout.topMargin: 4
                                visible: propertyRows.length === 0
                                text: detail.node ? "这个节点没有属性记录。" : ""
                                color: t.muted
                                font.pixelSize: 11
                            }
                        }
                    }

                    Rectangle {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 1
                        color: t.line
                    }

                    Label {
                        text: "节点类型"
                        color: t.muted
                        font.pixelSize: 11
                    }
                    ComboBox {
                        id: kindEditor
                        Layout.fillWidth: true
                        model: page.typeKeys.map(function (key) { return page.typeLabels[key] })
                        enabled: detail.node !== undefined && detail.node !== null
                    }
                    Button {
                        Layout.fillWidth: true
                        text: "提交类型修订"
                        enabled: page.selectedId.length > 0
                        onClicked: bridge.reviseNode(page.selectedId,
                            {node_type: page.typeKeys[kindEditor.currentIndex]})
                    }
                    Button {
                        Layout.fillWidth: true
                        text: "撤回该节点"
                        enabled: page.selectedId.length > 0
                        onClicked: bridge.reviseNode(page.selectedId, {retracted: true})
                    }
                }
            }
        }
    }
}
