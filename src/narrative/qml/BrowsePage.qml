import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts

// 数据浏览：左侧分类，中间节点列表（按阅读顺序排，编号按类型各成一套），右侧详情 + 属性。
Item {
    id: page
    objectName: "browsePage"
    property var app
    readonly property var t: app.theme

    property var nodes: []
    property var steps: []
    property var events: []
    property var nameIndex: ({})
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

    // 「最新」+ 当前粒度的每一步；编号即阅读顺序，所以顺序就是读者读到的顺序。
    readonly property var cutLabels: {
        var labels = ["最新"]
        for (var i = 0; i < steps.length; ++i)
            labels.push(steps[i].id + "  " + steps[i].name)
        return labels
    }

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
    // 工程里一个节点都没有（区别于"被条件过滤掉了"）。
    property int projectTotal: 0
    readonly property bool isEmptyProject: projectTotal === 0

    ListModel { id: rows }
    ListModel { id: categories }

    // Plain JS arrays, not ListModel: a record carries nested arrays (related
    // nodes) and ListModel drops those on the way in.
    property var propertyRows: []      // 扁平列表，「全部」视图用
    property var categoryGroups: []    // 按大类分组
    property var inboundRows: []       // 别的节点指向本节点的记录
    // 节点资料：总结、描述、形态与设定图。这是**编辑素材，不是原文披露**——
    // 没有引文、没有阅读位置，所以不进属性记录，也不跟阅读位置走。
    property var material: ({ summary: "", description: "", forms: [], kinds: [] })
    property string summaryDraft: ""
    property string descriptionDraft: ""
    property string savedSummary: ""
    property string savedDescription: ""
    property string materialNodeId: ""
    property string pendingFormId: ""
    property string pendingKind: ""
    property string formEditId: ""
    property bool materialSaving: false
    readonly property bool materialDirty: summaryDraft !== savedSummary
        || descriptionDraft !== savedDescription
    property var vocabulary: []        // 当前节点类型的分类表
    property bool groupByCategory: true
    // 属性区回答哪个问题：latest = 这一刻的状态；all = 到这里为止说过的全部，
    // 失效的也列出来并逐条标注。两者在能失效之后就不是一回事了。
    property var expandedProps: ({})
    property var pickerTargets: []
    property var mergeNames: []
    // 事件等级：场景 / 事件。默认场景（粒度更粗，一次读一段）。
    property string granularity: "scene"
    // 阅读位置：cutId 是场景或事件的编号，空串表示看到最新。
    property string cutId: ""
    property string cutEventId: ""
    property string cutName: ""
    property int cutLater: 0
    // 默认只列当前这一步的相关节点：读者看某一步时，要的是这一步里的人、物、事，
    // 不是全书 164 个节点。没有选步骤（停在「最新」）时不筛。
    property bool relatedOnly: true
    // 同一个阅读位置在两种粒度下的编号：切粒度时停在这里，不跳回「最新」。
    property var cutAlt: ({})
    // 节点列表是否跟着阅读位置收窄。默认全部显示。
    property bool timeFilter: false
    // 属性区上一次用的切点：搜索时切点没变就不重载，展开状态得以保留。
    property string appliedCut: ""
    property string pendingFocus: ""

    // 查询、过滤、计数、切点解析都在后端：界面只提交条件并渲染结果。
    function load() {
        bridge.loadBrowse({
            granularity: page.granularity,
            cut_id: page.cutId,
            node_type: page.selectedType,
            query: page.query,
            related_only: page.relatedOnly,
            time_filter: page.timeFilter
        })
    }

    function nameOf(id) {
        var name = page.nameIndex[id]
        return name === undefined ? id : name
    }

    function applyBrowse(payload) {
        revision = payload.revision === undefined ? revision : payload.revision
        steps = payload.steps || []
        events = payload.events || []
        counts = payload.counts || ({})
        projectTotal = payload.project_total === undefined ? 0 : payload.project_total
        total = payload.total || 0
        nodes = payload.nodes || []
        var cut = payload.cut || null
        cutEventId = cut ? cut.event_id : ""
        cutName = cut ? cut.step_name : ""
        cutAlt = payload.cut_alt || ({})
        if (!cut) cutLater = 0

        var index = page.nameIndex
        for (var i = 0; i < nodes.length; ++i)
            if (nodes[i].id) index[nodes[i].id] = nodes[i].name || nodes[i].id
        page.nameIndex = index

        rows.clear()
        for (var j = 0; j < nodes.length; ++j) {
            var node = nodes[j]
            rows.append({
                id: node.id || "",
                labelId: node.label_id || node.id || "",
                name: node.name || "未命名",
                kindLabel: page.typeLabels[node.node_type] || node.node_type || "节点",
                note: node.identity_note || ""
            })
        }
        rebuildCategories()
        syncCutIndex()

        // 收窄或过滤后选中的节点可能不在列表里：不能留着一个"此刻还不存在"的详情。
        if (page.selectedId.length > 0 && !nodes.some(function (node) {
            return node.id === page.selectedId
        })) {
            page.clearSelection()
        }
        if (page.pendingFocus.length > 0) {
            for (var k = 0; k < rows.count; ++k) {
                if (rows.get(k).id === page.pendingFocus) {
                    nodeList.positionViewAtIndex(k, ListView.CenterInView)
                    break
                }
            }
            page.pendingFocus = ""
        }
        // 属性区跟同一个锚点事件走；切点没变就不重载。
        if (page.selectedId.length > 0 && page.appliedCut !== page.cutEventId) {
            page.appliedCut = page.cutEventId
            bridge.loadNodeProperties(page.selectedId)
        }
    }

    function rebuildCategories() {
        var all = 0
        for (var name in counts) all += counts[name]
        categories.clear()
        categories.append({key: "", label: "全部", count: all})
        for (var i = 0; i < page.typeKeys.length; ++i) {
            var key = page.typeKeys[i]
            categories.append({key: key, label: page.typeLabels[key], count: counts[key] || 0})
        }
    }

    function syncCutIndex() {
        var index = 0
        for (var i = 0; i < page.steps.length; ++i)
            if (page.steps[i].id === page.cutId) { index = i + 1; break }
        if (cutBox.currentIndex !== index) cutBox.currentIndex = index
    }

    function clearSelection() {
        selectedId = ""
        detail = ({})
        propertyRows = []
        categoryGroups = []
        inboundRows = []
        expandedProps = ({})
        appliedCut = ""
    }

    function pickType(kind) {
        selectedType = kind
        page.load()
    }

    // 换粒度时停在同一个阅读位置：场景的切点是它的最后一个事件，事件的切点是
    // 包含它的场景。位置由后端算好（cut_alt），界面只负责换成那个编号。
    function pickGranularity(value) {
        if (page.granularity === value) return
        var alt = page.cutAlt || ({})
        page.granularity = value
        page.cutId = alt[value] || ""
        page.appliedCut = ""
        page.load()
    }

    // 步骤按编号排列（编号即阅读顺序），所以下拉里的顺序就是读者读到的顺序。
    function pickCut(index) {
        if (cutBox.currentIndex !== index) cutBox.currentIndex = index
        page.cutId = index > 0 ? page.steps[index - 1].id : ""
        page.cutLater = 0
        page.load()
    }

    function toggleTimeFilter() {
        page.timeFilter = !page.timeFilter
        page.load()
    }

    function toggleRelated() {
        page.relatedOnly = !page.relatedOnly
        page.load()
    }

    function backToLatest() {
        cutBox.currentIndex = 0
        page.pickCut(0)
    }

    // 上一个 / 下一个时间点。下拉里 0 是「最新」，1..N 是各步骤，
    // 所以时间顺序是 1 → N → 最新(0)，两头各自接上。
    function stepCut(delta) {
        var count = page.steps.length
        if (count === 0) return
        var index = cutBox.currentIndex
        if (delta < 0) {
            if (index === 0) index = count
            else if (index > 1) index -= 1
        } else {
            if (index === 0) return
            index = index < count ? index + 1 : 0
        }
        page.pickCut(index)
    }

    function select(id) {
        selectedId = id
        detail = ({})
        propertyRows = []
        categoryGroups = []
        inboundRows = []
        expandedProps = ({})
        appliedCut = page.cutEventId
        material = ({ summary: "", description: "", forms: [], kinds: [] })
        summaryDraft = ""
        descriptionDraft = ""
        savedSummary = ""
        savedDescription = ""
        materialNodeId = ""
        bridge.nodeDetail(id)
        bridge.loadNodeProperties(id)
        bridge.loadMaterial(id)
    }

    // 事件名：形态写的是编号，页面上要看到那一步叫什么。
    function eventName(id) {
        if (!id) return ""
        for (var i = 0; i < page.events.length; ++i)
            if (page.events[i].id === id) return page.events[i].name
        return id
    }

    function applyMaterial(payload) {
        // 慢结果不能覆盖别人：切到另一个节点后，上一个节点的资料会迟到。
        if (!payload || payload.node_id !== page.selectedId) return
        var dirtySummary = page.summaryDraft !== page.savedSummary
        var dirtyDescription = page.descriptionDraft !== page.savedDescription
        page.materialNodeId = payload.node_id
        page.material = payload
        page.savedSummary = payload.summary || ""
        page.savedDescription = payload.description || ""
        // 正在打字就不要把输入框冲掉，只跟没动过的那一份走。
        if (!dirtySummary) page.summaryDraft = page.savedSummary
        if (!dirtyDescription) page.descriptionDraft = page.savedDescription
        if (page.materialSaving) {
            page.materialSaving = false
            app.toast("资料已保存", false)
        }
    }

    function saveMaterialText() {
        if (page.selectedId.length === 0) return
        page.materialSaving = true
        bridge.saveMaterialText(page.selectedId, page.summaryDraft, page.descriptionDraft)
    }

    // 新增和编辑共用一张表单：编辑时把现值填进去。
    function openFormDialog(form) {
        page.formEditId = form ? (form.id || "") : ""
        formName.text = form ? (form.name || "") : ""
        formNote.text = form ? (form.note || "") : ""
        formEvent.text = form ? (form.event_id || "") : ""
        formDialog.open()
    }

    function submitForm() {
        if (page.selectedId.length === 0) return
        if (page.formEditId.length > 0)
            bridge.updateMaterialForm(page.selectedId, page.formEditId, formName.text,
                formNote.text, formEvent.text)
        else
            bridge.addMaterialForm(page.selectedId, formName.text, formNote.text,
                formEvent.text)
        page.formEditId = ""
        formName.text = ""
        formNote.text = ""
        formEvent.text = ""
        formDialog.close()
    }

    function askMaterialImage(formId, kind) {
        page.pendingFormId = formId
        page.pendingKind = kind
        materialImagePicker.open()
    }

    function valueText(item) {
        if (item.value_node) return item.value_node.name
        if (item.value === null || item.value === undefined) return "（空）"
        return String(item.value)
    }

    function buildItem(item) {
        var records = item.records || []
        var ids = []
        for (var i = 0; i < records.length; ++i) ids.push(records[i].id)
        var scopes = item.scopes || [item.scope || "world"]
        var scopeText = ""
        for (var s = 0; s < scopes.length; ++s) {
            scopeText += (s > 0 ? "/" : "")
                + (page.scopeLabels[scopes[s]] || scopes[s] || "世界")
        }
        return {
            // 同一方面可能分在两个大类里，key 要带分类，否则展开状态会串。
            key: (item.aspect || item.property_id) + "|" + (item.category || ""),
            // 中文名由后端给（property_labels），未知属性名回退到原名。
            label: item.label || page.propertyLabels[item.property_id] || item.property_id || "",
            names: (item.property_ids || []).length > 1 ? item.property_ids.join(" / ") : "",
            nameIds: item.property_ids || [],
            category: item.category || "其他",
            categorySource: item.category_source || "",
            otherCategories: item.other_categories || [],
            recordIds: ids,
            valueText: (item.is_current === false && item.retracted !== true
                        ? "（历史）" : "") + page.valueText(item),
            valueNodeId: item.value_node ? item.value_node.id : "",
            scopeText: scopes.length > 1 ? scopeText : (page.scopeLabels[item.scope] || "世界"),
            mixedScope: scopes.length > 1,
            holderId: item.holder_id || "",
            holderName: item.holder_name
                || (item.holder_id ? page.nameOf(item.holder_id) : ""),
            // 有效与否是记录上的显式标记，不是算出来的区间：这里只说披露在哪儿。
            disclosedText: item.retracted ? "" : ("披露于 " + (item.disclosed_at || "—")),
            records: records
        }
    }

    function applyProperties(payload) {
        var groups = payload.categories || []
        var built = []
        var all = []
        for (var g = 0; g < groups.length; ++g) {
            var items = []
            var source = groups[g].items || []
            for (var i = 0; i < source.length; ++i) {
                var row = page.buildItem(source[i])
                items.push(row)
                all.push(row)
            }
            built.push({
                key: groups[g].key, label: groups[g].key,
                count: groups[g].count || items.length, items: items
            })
        }
        vocabulary = payload.vocabulary || []
        inboundRows = payload.inbound || []
        cutLater = payload.later_count || 0
        categoryGroups = built
        propertyRows = all
        expandedProps = ({})
    }

    // 移动分类：只改整理位置，取值和披露顺序都不动。
    function askCategory(current, targets) {
        if (!targets || targets.length === 0) return
        pickerTargets = targets
        categoryPicker.current = current
        categoryPicker.open()
    }

    function applyCategory(category) {
        if (pickerTargets.length === 0) return
        bridge.categorizeProperties(JSON.stringify(pickerTargets), category)
    }

    // 属性归并：一行里出现多个名字时，选一个保留，其余按它读。
    // 归并和移动分类是两件事：一个改含义，一个只改位置。
    function askMerge(names) {
        if (!names || names.length < 2) return
        mergeNames = names
        mergeDialog.open()
    }

    function applyMerge(target) {
        for (var i = 0; i < mergeNames.length; ++i)
            if (mergeNames[i] !== target) bridge.mergeProperties(mergeNames[i], target)
        mergeDialog.close()
    }

    function openSource(sourceId, start) {
        app.openSource(sourceId, start)
    }

    // 改动落在阅读位置之后时要说清楚：记录确实写进去了，只是这一刻还读不到。
    function editToast(prefix, payload) {
        if (payload.outside_window !== true) return prefix
        return prefix + "，但它披露于「" + (payload.anchor_name || "该事件")
            + "」——在当前阅读位置之后，切到「最新」或那个事件才看得到"
    }

    function notifyEdit(prefix, payload) {
        app.toast(page.editToast(prefix, payload), payload.outside_window === true)
    }

    function openCategorySettings() {
        categoryEditor.text = page.vocabulary.join("\n")
        categoryDialog.open()
    }

    // 补录：人工写一条披露，仍然先成为候选。
    function eventIndex(id) {
        for (var i = 0; i < page.events.length; ++i)
            if (page.events[i].id === id) return i
        return -1
    }

    function openAddProperty() {
        addPropertyField.text = ""
        addValueField.text = ""
        addQuoteField.text = ""
        addCategoryField.currentIndex = Math.max(0, page.vocabulary.indexOf("其他"))
        // 停在某个阅读位置时，补的就是「此刻」：默认披露在这个事件，而不是第一个事件。
        var here = page.cutEventId.length > 0 ? page.eventIndex(page.cutEventId) : -1
        addEventField.currentIndex = here >= 0 ? here : 0
        addPropertyDialog.open()
    }

    function submitAddProperty() {
        var event = page.events[addEventField.currentIndex] || ({})
        var node = page.detail.node || ({})
        var sourceId = node.evidence ? node.evidence.source_id
            : (node.disclosure ? node.disclosure.source_id : "")
        bridge.addPropertyRecord({
            node_id: page.selectedId,
            property_id: addPropertyField.text,
            value: addValueField.text,
            event_id: event.id || "",
            category: addCategoryField.currentText,
            source: addSourceField.currentText,
            quote: addQuoteField.text,
            source_id: sourceId || ""
        })
        // 不在这里关：预检没过时要把填好的内容留在框里，等结果再决定。
    }

    function saveCategorySettings() {
        var lines = categoryEditor.text.split("\n")
        var names = []
        for (var i = 0; i < lines.length; ++i) {
            var name = lines[i].trim()
            if (name.length > 0 && name !== "其他" && names.indexOf(name) < 0) names.push(name)
        }
        if (names.length === 0) {
            app.toast("至少要保留一个大类", true)
            return
        }
        var config = {}
        config[detail.node ? detail.node.node_type : "other"] = names
        bridge.savePropertyCategories(JSON.stringify(config))
        categoryDialog.close()
    }

    function jumpTo(id) {
        if (!id) return
        // 目标节点可能被当前条件挡在列表外：只有真的挡住了才清条件。
        // 「相关节点」现在是默认开的，无条件清掉会让默认筛选项被一次跳转悄悄关掉。
        var listed = false
        for (var i = 0; i < page.nodes.length; ++i)
            if (page.nodes[i].id === id) { listed = true; break }
        if (!listed) {
            page.selectedType = ""
            page.query = ""
            page.relatedOnly = false
            searchField.text = ""
        }
        page.pendingFocus = id
        page.load()
        page.select(id)
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
                categoryGroups = []
                inboundRows = []
                expandedProps = ({})
                page.cutId = ""
                page.cutEventId = ""
                page.cutName = ""
                page.cutLater = 0
                page.appliedCut = ""
                page.cutAlt = ({})
                page.relatedOnly = true
                page.timeFilter = false
                page.granularity = "scene"
                page.load()
            }
        }
    }

    Connections {
        target: bridge
        function onResultReady(kind, payload) {
            if (kind === "browse") {
                page.applyBrowse(payload)
            } else if (kind === "detail") {
                detail = payload
            } else if (kind === "property_correct") {
                if (payload.status === "accepted") {
                    app.toast(page.editToast("更正已生效", payload), payload.outside_window === true)
                    if (page.selectedId.length > 0) bridge.loadNodeProperties(page.selectedId)
                } else {
                    var problem = (payload.diagnostics || [])[0] || ({})
                    app.toast("更正未通过预检：" + (problem.message || "未知原因"), true)
                }
            } else if (kind === "property_retract") {
                var removed = (payload.retracted || []).length
                // 0 条不是「成功了但没说」，是「这些记录本来就已经失效过」。
                app.toast(removed > 0
                    ? "已撤回 " + removed + " 条记录"
                    : "这些记录已经失效过，不用再撤", removed === 0)
                if (page.selectedId.length > 0) bridge.loadNodeProperties(page.selectedId)
            } else if (kind === "material") {
                page.applyMaterial(payload)
            } else if (kind === "node_properties") {
                page.applyProperties(payload)
            } else if (kind === "property_categorize") {
                app.toast("已移到「" + (payload.category || "") + "」", false)
                if (page.selectedId.length > 0) bridge.loadNodeProperties(page.selectedId)
            } else if (kind === "property_merge") {
                app.toast("已合并到「" + (payload.target_id || "") + "」", false)
                if (page.selectedId.length > 0) bridge.loadNodeProperties(page.selectedId)
            } else if (kind === "property_add") {
                if (payload.status === "accepted") {
                    addPropertyDialog.close()
                    app.toast(page.editToast("补录已生效", payload), payload.outside_window === true)
                    if (page.selectedId.length > 0) bridge.loadNodeProperties(page.selectedId)
                } else {
                    var first = (payload.diagnostics || [])[0] || ({})
                    app.toast("补录未通过预检：" + (first.message || "未知原因"), true)
                }
            } else if (kind === "property_categories") {
                var nodeType = page.detail.node ? page.detail.node.node_type : "other"
                page.vocabulary = (payload.vocabulary || ({}) )[nodeType] || page.vocabulary
                if (payload.saved) {
                    app.toast("分类表已保存", false)
                    if (page.selectedId.length > 0) bridge.loadNodeProperties(page.selectedId)
                }
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
                Layout.preferredWidth: 280
                placeholderText: "搜索名称或别名"
                // 输入停下来再查，避免每个字都跑一次后端；回车就不等了。
                onTextChanged: { page.query = text; searchTimer.restart() }
                onAccepted: { searchTimer.stop(); page.load() }
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
            spacing: 8

            // 事件等级：决定「阅读位置」按场景还是按事件走。
            Label {
                text: "事件等级"
                color: t.muted
                font.pixelSize: 12
            }
            Button {
                objectName: "browseGranularityScene"
                text: "场景"
                flat: page.granularity !== "scene"
                highlighted: page.granularity === "scene"
                font.pixelSize: 11
                onClicked: page.pickGranularity("scene")
            }
            Button {
                objectName: "browseGranularityEvent"
                text: "事件"
                flat: page.granularity !== "event"
                highlighted: page.granularity === "event"
                font.pixelSize: 11
                onClicked: page.pickGranularity("event")
            }
            // 列表是否跟着阅读位置收窄。
            Button {
                objectName: "browseTimeFilter"
                text: page.timeFilter ? "按时间显示" : "全部显示"
                flat: !page.timeFilter
                highlighted: page.timeFilter
                font.pixelSize: 11
                onClicked: page.toggleTimeFilter()
            }
            Label {
                text: "阅读位置"
                color: t.muted
                font.pixelSize: 12
            }
            Button {
                objectName: "browseCutPrev"
                text: "‹"
                flat: true
                font.pixelSize: 15
                enabled: page.steps.length > 0
                    && (cutBox.currentIndex > 1 || cutBox.currentIndex === 0)
                onClicked: page.stepCut(-1)
            }
            ComboBox {
                id: cutBox
                objectName: "browseCutBox"
                Layout.fillWidth: true
                Layout.maximumWidth: 320
                model: page.cutLabels
                enabled: page.steps.length > 0
                onActivated: page.pickCut(currentIndex)
            }
            Button {
                objectName: "browseCutNext"
                text: "›"
                flat: true
                font.pixelSize: 15
                enabled: page.steps.length > 0 && cutBox.currentIndex !== 0
                onClicked: page.stepCut(1)
            }
            Button {
                objectName: "browseCutReset"
                text: "最新"
                flat: true
                enabled: page.cutEventId.length > 0
                onClicked: page.backToLatest()
            }
            CheckBox {
                id: relatedBox
                objectName: "browseRelatedOnly"
                text: "相关节点"
                checked: page.relatedOnly
                font.pixelSize: 11
                onToggled: page.toggleRelated()
            }
            Item { Layout.fillWidth: true }
        }

        // 搜索防抖：输入停下 200ms 才查。
        Timer {
            id: searchTimer
            interval: 200
            onTriggered: page.load()
        }

        // 三栏都能拖：分类 / 列表 / 详情。宽度是界面自己的事，不落后端。
        SplitView {
            id: columns
            objectName: "browseColumns"
            Layout.fillWidth: true
            Layout.fillHeight: true
            orientation: Qt.Horizontal

            handle: Rectangle {
                objectName: "browseColumnHandle"
                implicitWidth: 12
                color: "transparent"

                // 只负责把光标变成左右箭头；按键留给 SplitView 自己处理。
                MouseArea {
                    anchors.fill: parent
                    cursorShape: Qt.SplitHCursor
                    acceptedButtons: Qt.NoButton
                }

                Rectangle {
                    anchors.centerIn: parent
                    width: 2
                    height: parent.height
                    radius: 1
                    color: SplitHandle.pressed || SplitHandle.hovered ? t.accent : t.line
                }
            }

            // -- categories ---------------------------------------------
            Rectangle {
                SplitView.preferredWidth: 190
                SplitView.minimumWidth: 140
                SplitView.fillHeight: true
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
                SplitView.fillWidth: true
                SplitView.minimumWidth: 240
                SplitView.fillHeight: true
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
                        required property string labelId
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
                                text: labelId
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
                        text: page.isEmptyProject ? "这个工程还没有节点。" : "该分类下没有节点。"
                        color: t.muted
                    }
                }
            }

            // -- detail --------------------------------------------------
            Rectangle {
                SplitView.preferredWidth: 500
                SplitView.minimumWidth: 360
                SplitView.fillHeight: true
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
                            ? "编号 " + (detail.node.label_id || detail.node.id) + " · "
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

                            // 阅读位置生效时说明清楚：这是"读到这里"的状态，不是全部。
                            Label {
                                Layout.fillWidth: true
                                Layout.topMargin: 2
                                visible: page.cutEventId.length > 0
                                wrapMode: Text.Wrap
                                text: "读到「" + (page.cutName || page.cutEventId) + "」时的状态"
                                    + (cutBox.currentIndex > 0
                                        ? "（第 " + cutBox.currentIndex + " / "
                                            + page.steps.length + " 个"
                                            + (page.granularity === "scene" ? "场景" : "事件")
                                            + "）" : "")
                                color: t.warn
                                font.pixelSize: 11
                            }

                            // 属性标题 + 整理动作一行放得下（详情栏窄时会被压，按钮各自有下限）。
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 6
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
                                Button {
                                    objectName: "categoryModeButton"
                                    text: page.groupByCategory ? "分组" : "平铺"
                                    flat: true
                                    font.pixelSize: 10
                                    onClicked: page.groupByCategory = !page.groupByCategory
                                }
                                Button {
                                    objectName: "categorySettingsButton"
                                    text: "分类表"
                                    flat: true
                                    font.pixelSize: 10
                                    enabled: detail.node !== undefined && detail.node !== null
                                    onClicked: page.openCategorySettings()
                                }
                                Button {
                                    objectName: "addPropertyButton"
                                    text: "补录"
                                    flat: true
                                    font.pixelSize: 10
                                    enabled: page.selectedId.length > 0 && page.events.length > 0
                                    onClicked: page.openAddProperty()
                                }
                            }

                            // 按大类：一个大类一行标题，下面是它底下的属性。
                            // 分类只是整理位置，取值和披露顺序都不受影响。
                            Repeater {
                                objectName: "browseCategoryGroups"
                                model: page.groupByCategory ? page.categoryGroups : []

                                delegate: Column {
                                    required property var modelData

                                    Layout.fillWidth: true
                                    Layout.topMargin: 2
                                    spacing: 0
                                    visible: modelData.items.length > 0

                                    RowLayout {
                                        width: parent.width
                                        spacing: 6
                                        Label {
                                            text: modelData.label
                                            color: t.muted
                                            font.pixelSize: 11
                                            font.weight: Font.DemiBold
                                        }
                                        Label {
                                            text: modelData.count
                                            color: t.muted
                                            font.pixelSize: 10
                                        }
                                        Rectangle {
                                            Layout.fillWidth: true
                                            Layout.preferredHeight: 1
                                            color: t.line
                                        }
                                    }
                                    Repeater {
                                        model: modelData.items

                                        delegate: PropertyItem {
                                            required property var modelData
                                            width: parent.width
                                            row: modelData
                                            host: page
                                        }
                                    }
                                }
                            }

                            // 全部：不分组，按属性名排。
                            Repeater {
                                model: page.groupByCategory ? [] : page.propertyRows

                                delegate: PropertyItem {
                                    required property var modelData
                                    width: parent.width
                                    row: modelData
                                    host: page
                                }
                            }

                            // 反向引用：别的节点上的记录指向本节点。同一份记录，
                            // 第二个入口，不复制、不改披露位置。
                            Column {
                                Layout.fillWidth: true
                                Layout.topMargin: 6
                                spacing: 2
                                visible: inboundRows.length > 0

                                RowLayout {
                                    width: parent.width
                                    spacing: 6
                                    Label {
                                        text: "反向引用"
                                        color: t.muted
                                        font.pixelSize: 11
                                        font.weight: Font.DemiBold
                                    }
                                    Label {
                                        text: inboundRows.length
                                        color: t.muted
                                        font.pixelSize: 10
                                    }
                                    Rectangle {
                                        Layout.fillWidth: true
                                        Layout.preferredHeight: 1
                                        color: t.line
                                    }
                                }
                                Repeater {
                                    objectName: "browseInboundRows"
                                    model: inboundRows

                                    delegate: RowLayout {
                                        required property var modelData
                                        width: parent.width
                                        spacing: 6

                                        Label {
                                            text: modelData.category
                                            color: t.muted
                                            font.pixelSize: 10
                                            Layout.preferredWidth: 76
                                            elide: Text.ElideRight
                                        }
                                        Label {
                                            text: modelData.node_name
                                            color: t.accent
                                            font.pixelSize: 11
                                            font.underline: true
                                            MouseArea {
                                                anchors.fill: parent
                                                cursorShape: Qt.PointingHandCursor
                                                onClicked: page.jumpTo(modelData.node_id)
                                            }
                                        }
                                        Label {
                                            text: "· " + (modelData.property_label
                                                || page.propertyLabels[modelData.property_id]
                                                || modelData.property_id) + " = "
                                                + (modelData.node_type ? "" : "")
                                                + page.nameOf(modelData.value)
                                            color: t.body
                                            font.pixelSize: 11
                                            elide: Text.ElideRight
                                            Layout.fillWidth: true
                                        }
                                    }
                                }
                            }
                            // 没选中节点时不摆空表单：这一块是给具体节点写的。
                            ColumnLayout {
                                Layout.fillWidth: true
                                visible: page.selectedId.length > 0
                                spacing: 8

                                // 资料：总结、描述、形态与设定图。这是编辑素材，不是原文披露——
                                // 没有引文，也不跟阅读位置走，所以单独一块，不混进属性。
                                Rectangle {
                                    Layout.fillWidth: true
                                    Layout.preferredHeight: 1
                                    Layout.topMargin: 8
                                    color: t.line
                                }

                                RowLayout {
                                    Layout.fillWidth: true
                                    spacing: 6
                                    Label {
                                        text: "资料"
                                        color: t.ink
                                        font.pixelSize: 13
                                        font.weight: Font.DemiBold
                                    }
                                    Label {
                                        text: page.material.forms.length > 0
                                            ? "形态 " + page.material.forms.length : ""
                                        color: t.muted
                                        font.pixelSize: 11
                                    }
                                    Item { Layout.fillWidth: true }
                                    Button {
                                        objectName: "materialSave"
                                        text: "保存"
                                        flat: true
                                        font.pixelSize: 10
                                        enabled: page.selectedId.length > 0 && page.materialDirty
                                        onClicked: page.saveMaterialText()
                                    }
                                }
                                Label {
                                    Layout.fillWidth: true
                                    text: "只给人和程序看：没有引文，也不跟阅读位置走，不进属性记录。"
                                    color: t.muted
                                    font.pixelSize: 10
                                    wrapMode: Text.Wrap
                                }
                                TextField {
                                    objectName: "materialSummary"
                                    Layout.fillWidth: true
                                    placeholderText: "一句话说清这是什么"
                                    text: page.summaryDraft
                                    onTextChanged: page.summaryDraft = text
                                    onAccepted: page.saveMaterialText()
                                }
                                TextArea {
                                    objectName: "materialDescription"
                                    Layout.fillWidth: true
                                    Layout.preferredHeight: 96
                                    placeholderText: "写详细一点：来历、特征、脾气、要注意的地方……"
                                    wrapMode: TextArea.Wrap
                                    text: page.descriptionDraft
                                    onTextChanged: page.descriptionDraft = text
                                }

                                RowLayout {
                                    Layout.fillWidth: true
                                    spacing: 6
                                    Label {
                                        text: "形态"
                                        color: t.ink
                                        font.pixelSize: 12
                                        font.weight: Font.DemiBold
                                    }
                                    Label {
                                        text: "同一个对象在不同时候的样子，可指定从哪个事件起"
                                        color: t.muted
                                        font.pixelSize: 10
                                        elide: Text.ElideRight
                                        Layout.fillWidth: true
                                        Layout.minimumWidth: 0
                                    }
                                    Button {
                                        objectName: "materialAddForm"
                                        text: "新增形态"
                                        flat: true
                                        font.pixelSize: 10
                                        enabled: page.selectedId.length > 0
                                        onClicked: page.openFormDialog(null)
                                    }
                                }

                                Repeater {
                                    objectName: "materialForms"
                                    model: page.material.forms

                                    delegate: Rectangle {
                                        required property var modelData
                                        // 里面几层 Repeater 会覆盖 modelData，先留住这一版形态。
                                        readonly property var form: modelData
                                        Layout.fillWidth: true
                                        implicitHeight: formBlock.implicitHeight + 20
                                        radius: 6
                                        color: t.paper
                                        border.color: t.line

                                        ColumnLayout {
                                            id: formBlock
                                            anchors.fill: parent
                                            anchors.margins: 10
                                            spacing: 6

                                            RowLayout {
                                                Layout.fillWidth: true
                                                spacing: 6
                                                Label {
                                                    text: form.name
                                                    color: t.ink
                                                    font.pixelSize: 12
                                                    font.weight: Font.DemiBold
                                                }
                                                Label {
                                                    text: form.event_id
                                                        ? "从「" + page.eventName(form.event_id) + "」起"
                                                        : "未指定时间点"
                                                    color: t.muted
                                                    font.pixelSize: 10
                                                    elide: Text.ElideRight
                                                    Layout.fillWidth: true
                                                    Layout.minimumWidth: 0
                                                }
                                                Label {
                                                    text: "编辑"
                                                    color: t.accent
                                                    font.pixelSize: 10
                                                    font.underline: true
                                                    MouseArea {
                                                        anchors.fill: parent
                                                        cursorShape: Qt.PointingHandCursor
                                                        onClicked: page.openFormDialog(form)
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
                                                        onClicked: bridge.removeMaterialForm(
                                                            page.selectedId, form.id)
                                                    }
                                                }
                                            }
                                            Label {
                                                Layout.fillWidth: true
                                                visible: (form.note || "").length > 0
                                                text: form.note || ""
                                                color: t.body
                                                font.pixelSize: 11
                                                wrapMode: Text.Wrap
                                            }

                                            // 六类图：按钮一行，已挂的图在下面。
                                            Flow {
                                                Layout.fillWidth: true
                                                spacing: 6
                                                Repeater {
                                                    model: page.material.kinds

                                                    delegate: Button {
                                                        required property var modelData
                                                        text: "+ " + modelData.label
                                                        flat: true
                                                        font.pixelSize: 10
                                                        onClicked: page.askMaterialImage(
                                                            form.id, modelData.key)
                                                    }
                                                }
                                            }
                                            Flow {
                                                Layout.fillWidth: true
                                                spacing: 8
                                                Repeater {
                                                    model: form.images

                                                    delegate: Column {
                                                        required property var modelData
                                                        spacing: 2
                                                        Image {
                                                            width: 104
                                                            height: 104
                                                            source: modelData.path
                                                                ? "file:///" + String(modelData.path).replace(/\\/g, "/")
                                                                : ""
                                                            fillMode: Image.PreserveAspectFit
                                                            asynchronous: true
                                                        }
                                                        Label {
                                                            width: 104
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
                                                                onClicked: bridge.removeMaterialImage(
                                                                    page.selectedId, form.id, modelData.id)
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
                                    visible: page.selectedId.length > 0
                                        && page.material.forms.length === 0
                                    text: "还没有形态。可以给同一个对象留几版样子（换装、损毁、修复），各挂自己的图。"
                                    color: t.muted
                                    font.pixelSize: 10
                                    wrapMode: Text.Wrap
                                }
                            }
                        }
                    }

                    Rectangle {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 1
                        color: t.line
                    }

                    // 节点字段、属性补录/更正/失效都在编辑器里，这里只留一个入口。
                    Button {
                        objectName: "browseOpenEditor"
                        Layout.fillWidth: true
                        text: "编辑节点"
                        enabled: page.selectedId.length > 0
                        onClicked: nodeEditor.openFor(page.selectedId)
                    }
                }
            }
        }
    }

    // 选图：拷进工程旁边，不引用你选的原路径。
    FileDialog {
        id: materialImagePicker
        title: "选择图片"
        nameFilters: ["图片 (*.png *.jpg *.jpeg *.webp *.gif)"]
        onAccepted: {
            if (page.pendingFormId.length > 0 && page.selectedId.length > 0)
                bridge.addMaterialImage(page.selectedId, page.pendingFormId,
                    page.pendingKind, materialImagePicker.selectedFile.toString(), "")
        }
    }

    // 新增 / 编辑形态
    Dialog {
        id: formDialog
        objectName: "materialFormDialog"
        title: page.formEditId.length > 0 ? "编辑形态" : "新增形态"
        modal: true
        anchors.centerIn: parent
        width: 380

        contentItem: ColumnLayout {
            spacing: 6
            Label {
                Layout.fillWidth: true
                text: "形态是同一个对象在不同时候的样子，可以指定从哪个事件起成立。"
                color: t.muted
                font.pixelSize: 11
                wrapMode: Text.Wrap
            }
            TextField {
                id: formName
                objectName: "materialFormName"
                Layout.fillWidth: true
                placeholderText: "形态名，例如：青衫少年 / 断裂的剑"
                onAccepted: page.submitForm()
            }
            TextField {
                id: formNote
                objectName: "materialFormNote"
                Layout.fillWidth: true
                placeholderText: "设定：样子、材质、气质……"
                onAccepted: page.submitForm()
            }
            TextField {
                id: formEvent
                objectName: "materialFormEvent"
                Layout.fillWidth: true
                placeholderText: "从哪个事件起成立（可空，例如 34）"
                onAccepted: page.submitForm()
            }
            RowLayout {
                Layout.fillWidth: true
                Item { Layout.fillWidth: true }
                Button { text: "取消"; flat: true; onClicked: formDialog.close() }
                Button {
                    objectName: "materialFormSubmit"
                    text: page.formEditId.length > 0 ? "保存" : "新增"
                    highlighted: true
                    onClicked: page.submitForm()
                }
            }
        }
    }

    NodeEditor {
        id: nodeEditor
        host: page
    }

    // 移动分类：只改整理位置。取值、披露顺序、原文引文都不动。
    Dialog {
        id: categoryPicker
        objectName: "categoryPicker"
        property string current: ""
        title: "移到哪个大类"
        modal: true
        anchors.centerIn: parent
        width: 320

        contentItem: ColumnLayout {
            spacing: 4
            Label {
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                text: "分类只决定显示位置，不改取值，也不改披露顺序。"
                color: t.muted
                font.pixelSize: 11
            }
            Repeater {
                model: page.vocabulary
                delegate: Button {
                    required property string modelData
                    Layout.fillWidth: true
                    flat: true
                    highlighted: modelData === categoryPicker.current
                    text: modelData + (modelData === categoryPicker.current ? "  ·当前" : "")
                    onClicked: {
                        page.applyCategory(modelData)
                        categoryPicker.close()
                    }
                }
            }
            Button {
                Layout.fillWidth: true
                flat: true
                text: "取消"
                onClicked: categoryPicker.close()
            }
        }
    }

    // 归并属性名：同一个方面用了两个名字时，选一个保留。
    Dialog {
        id: mergeDialog
        objectName: "mergeDialog"
        title: "合并成一个名字"
        modal: true
        anchors.centerIn: parent
        width: 340

        contentItem: ColumnLayout {
            spacing: 4
            Label {
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                text: "选一个保留的名字。其余名字的记录照旧保存，只是按这个名字读；"
                    + "撤回这次合并就恢复成两个名字。"
                color: t.muted
                font.pixelSize: 11
            }
            Repeater {
                model: page.mergeNames
                delegate: Button {
                    required property string modelData
                    Layout.fillWidth: true
                    flat: true
                    text: modelData
                    onClicked: page.applyMerge(modelData)
                }
            }
            Button {
                Layout.fillWidth: true
                flat: true
                text: "取消"
                onClicked: mergeDialog.close()
            }
        }
    }

    // 分类表：每行一个大类，顺序即显示顺序；「其他」固定在最后。
    Dialog {
        id: categoryDialog
        objectName: "categoryDialog"
        title: "分类表 · " + (detail.node
            ? (page.typeLabels[detail.node.node_type] || detail.node.node_type) : "")
        modal: true
        anchors.centerIn: parent
        width: 380

        contentItem: ColumnLayout {
            spacing: 6
            Label {
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                text: "每行一个大类，顺序即显示顺序。「其他」始终在最后，不用写。"
                color: t.muted
                font.pixelSize: 11
            }
            ScrollView {
                Layout.fillWidth: true
                Layout.preferredHeight: 220
                TextArea {
                    id: categoryEditor
                    objectName: "categoryEditor"
                    wrapMode: TextArea.NoWrap
                    font.pixelSize: 13
                    selectByMouse: true
                }
            }
            RowLayout {
                Layout.fillWidth: true
                Item { Layout.fillWidth: true }
                Button {
                    text: "取消"
                    flat: true
                    onClicked: categoryDialog.close()
                }
                Button {
                    objectName: "categorySaveButton"
                    text: "保存"
                    highlighted: true
                    onClicked: page.saveCategorySettings()
                }
            }
        }
    }

    // 补录：人工写一条披露。它仍然先成为候选，接受后才进正式资料。
    Dialog {
        id: addPropertyDialog
        objectName: "addPropertyDialog"
        title: "补一条属性"
        modal: true
        anchors.centerIn: parent
        width: 460

        contentItem: ColumnLayout {
            spacing: 6

            Label {
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                text: "引文逐字复制，程序会核对位置；通过后立即生效（和它同一方面的更早的值，从这次披露起失效）。"
                color: t.muted
                font.pixelSize: 11
            }
            GridLayout {
                Layout.fillWidth: true
                columns: 2
                columnSpacing: 8
                rowSpacing: 6

                Label { text: "属性名"; color: t.muted; font.pixelSize: 11 }
                TextField {
                    id: addPropertyField
                    objectName: "addPropertyField"
                    Layout.fillWidth: true
                    placeholderText: "例如 state"
                    onAccepted: page.submitAddProperty()
                }
                Label { text: "值"; color: t.muted; font.pixelSize: 11 }
                TextField {
                    id: addValueField
                    objectName: "addValueField"
                    Layout.fillWidth: true
                    placeholderText: "照原文写"
                    onAccepted: page.submitAddProperty()
                }
                Label { text: "披露事件"; color: t.muted; font.pixelSize: 11 }
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 2
                    ComboBox {
                        id: addEventField
                        objectName: "addEventField"
                        Layout.fillWidth: true
                        model: page.events.map(function (item) { return item.name })
                    }
                    Label {
                        Layout.fillWidth: true
                        visible: page.cutEventId.length > 0
                        wrapMode: Text.Wrap
                        text: "默认是当前阅读位置「" + (page.cutName || "该事件") + "」"
                        color: t.muted
                        font.pixelSize: 9
                    }
                }
                Label { text: "大类"; color: t.muted; font.pixelSize: 11 }
                ComboBox {
                    id: addCategoryField
                    objectName: "addCategoryField"
                    Layout.fillWidth: true
                    model: page.vocabulary
                }
                Label { text: "来源"; color: t.muted; font.pixelSize: 11 }
                ComboBox {
                    id: addSourceField
                    objectName: "addSourceField"
                    Layout.fillWidth: true
                    model: ["narration", "speech", "thought", "observation", "document"]
                }
                Label { text: "原文引文"; color: t.muted; font.pixelSize: 11 }
                TextField {
                    id: addQuoteField
                    objectName: "addQuoteField"
                    Layout.fillWidth: true
                    placeholderText: "逐字复制；可留空，但补上才能回查"
                    onAccepted: page.submitAddProperty()
                }
            }
            RowLayout {
                Layout.fillWidth: true
                Item { Layout.fillWidth: true }
                Button {
                    text: "取消"
                    flat: true
                    onClicked: addPropertyDialog.close()
                }
                Button {
                    objectName: "addPropertySubmit"
                    text: "提交补录"
                    highlighted: true
                    onClicked: page.submitAddProperty()
                }
            }
        }
    }
}
