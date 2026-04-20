(function () {
    const nameInput = document.getElementById('dcbName');
    if (!nameInput) {
        return;
    }

    const REQUIRED_FEATURE_REFS = [
        'ghcr.io/devcontainers/features/docker-in-docker:2.16.1',
        'ghcr.io/devcontainers/features/github-cli:1.1.0',
        'ghcr.io/devcontainers/features/python:1.8.0',
        'ghcr.io/devcontainers-extra/features/wget-apt-get:1.0.17'
    ];

    const KUBERNETES_REQUIRED_FEATURE_REFS = [
        'ghcr.io/mpriscella/features/kind:1.0.1',
        'ghcr.io/devcontainers/features/kubectl-helm-minikube:1'
    ];

    const PRESETS = {
        base: [
            'ghcr.io/devcontainers/features/docker-in-docker:2.16.1',
            'ghcr.io/devcontainers/features/github-cli:1.1.0'
        ],
        kubernetes: [
            'ghcr.io/devcontainers/features/docker-in-docker:2.16.1',
            'ghcr.io/devcontainers/features/github-cli:1.1.0',
            'ghcr.io/mpriscella/features/kind:1.0.1',
            'ghcr.io/devcontainers/features/kubectl-helm-minikube:1'
        ]
    };

    const RECOMMENDED_BY_PROFILE = {
        base: [
            'ghcr.io/devcontainers/features/node:1',
            'ghcr.io/agardnerIT/devcontainer-feature-dtctl/dtctl:1'
        ],
        kubernetes: [
            'ghcr.io/mpriscella/features/kind:1.0.1',
            'ghcr.io/devcontainers/features/kubectl-helm-minikube:1',
            'ghcr.io/agardnerIT/devcontainer-feature-dtctl/dtctl:1'
        ]
    };

    const DRAFT_STORAGE_KEY = 'dcb-draft-v2';

    const state = {
        catalog: [],
        filteredCatalog: [],
        selectedMap: new Map(),
        profile: 'base'
    };

    const elements = {
        baseImageInput: document.getElementById('dcbBaseImage'),
        includeGitIgnoreInput: document.getElementById('dcbIncludeGitIgnore'),
        searchInput: document.getElementById('dcbSearch'),
        recommendedOnlyInput: document.getElementById('dcbRecommendedOnly'),
        featureSelect: document.getElementById('dcbFeatureSelect'),
        addFeatureButton: document.getElementById('dcbAddFeatureBtn'),
selectedFeaturePills: document.getElementById('dcbSelectedFeaturePills'),
        featureCount: document.getElementById('dcbFeatureCount'),
        configCards: document.getElementById('dcbConfigCards'),
        preview: document.getElementById('dcbPreview'),
        message: document.getElementById('dcbMessage'),
        spinner: document.getElementById('dcbSpinner'),
        downloadButton: document.getElementById('dcbDownloadBtn'),
        copyJsonButton: document.getElementById('dcbCopyJson'),
        resetButton: document.getElementById('dcbResetBtn'),
        summaryName: document.getElementById('dcbSummaryName'),
        summaryImage: document.getElementById('dcbSummaryImage'),
        summaryFiles: document.getElementById('dcbSummaryFiles'),
        summaryCount: document.getElementById('dcbSummaryCount'),
        summaryFeatureList: document.getElementById('dcbSummaryFeatureList'),
        generateMeta: document.getElementById('dcbGenerateMeta'),
        foundationList: document.getElementById('dcbFoundationList'),
        presetBaseButton: document.getElementById('dcbPresetBase'),
        presetKubernetesButton: document.getElementById('dcbPresetK8s'),
        forwardPortsInput: document.getElementById('dcbForwardPorts'),
        postCreateInput: document.getElementById('dcbPostCreateCommand'),
        postAttachInput: document.getElementById('dcbPostAttachCommand'),
        hostCpusInput: document.getElementById('dcbHostCpus'),
        hostMemoryInput: document.getElementById('dcbHostMemory'),
        hostStorageInput: document.getElementById('dcbHostStorage'),
        hostGpuInput: document.getElementById('dcbHostGpu')
    };

    const quickPickButtons = Array.from(document.querySelectorAll('.dcb-quick-pick'));

    function showMessage(type, text) {
        elements.message.textContent = text;
        elements.message.className = 'dcb-message is-visible ' + (type === 'error' ? 'is-error' : 'is-success');
    }

    function hideMessage() {
        elements.message.textContent = '';
        elements.message.className = 'dcb-message';
    }

    function sanitizeOptionValue(optionMeta, value) {
        if (optionMeta.type === 'boolean') {
            return Boolean(value);
        }

        if (optionMeta.type === 'number' || optionMeta.type === 'integer') {
            if (value === '' || value == null) {
                return '';
            }
            return Number(value);
        }

        return value == null ? '' : String(value);
    }

    function cloneDefaultOptions(feature) {
        const options = {};
        Object.entries(feature.options || {}).forEach(([optionName, optionMeta]) => {
            options[optionName] = sanitizeOptionValue(optionMeta, optionMeta.default);
        });
        return options;
    }

    function isRequiredFeature(reference) {
        return REQUIRED_FEATURE_REFS.includes(reference);
    }

    function getLockedFeatureRefsForCurrentProfile() {
        const locked = [...REQUIRED_FEATURE_REFS];
        if (state.profile === 'kubernetes') {
            locked.push(...KUBERNETES_REQUIRED_FEATURE_REFS);
        }
        return locked;
    }

    function isLockedFeatureForCurrentProfile(reference) {
        return getLockedFeatureRefsForCurrentProfile().includes(reference);
    }

    function getProfileDefaults(profile) {
        if (profile === 'kubernetes') {
            return {
                forwardPorts: [8080],
                portsAttributes: {
                    '8080': { label: 'OTEL Demo' }
                },
                hostRequirements: {
                    cpus: 2
                },
                postCreateCommand: 'pip install -r .devcontainer/requirements.txt && python environment_installer.py',
                postAttachCommand: 'python on_attach.py',
                secrets: {
                    DT_ENVIRONMENT_ID: {
                        description: 'eg. abc12345 from https://abc12345.live.dynatrace.com'
                    },
                    DT_ENVIRONMENT_TYPE: {
                        description: 'eg. live, sprint or dev. If unsure, use live.'
                    },
                    DT_API_TOKEN: {
                        description: 'Dynatrace API token'
                    }
                }
            };
        }

        return {
            forwardPorts: [],
            portsAttributes: {},
            hostRequirements: {},
            postCreateCommand: 'pip install -r .devcontainer/requirements.txt && python environment_installer.py',
            postAttachCommand: 'python on_attach.py',
            secrets: {}
        };
    }

    function parsePorts(inputValue) {
        if (!inputValue || !inputValue.trim()) {
            return [];
        }

        return inputValue
            .split(',')
            .map((part) => Number(part.trim()))
            .filter((part) => Number.isInteger(part) && part > 0);
    }

    function getAdvancedOverrides() {
        const hostRequirements = {};
        const cpus = parseInt(elements.hostCpusInput.value, 10);
        if (cpus > 0) { hostRequirements.cpus = cpus; }
        const memory = (elements.hostMemoryInput.value || '').trim();
        if (memory) { hostRequirements.memory = memory; }
        const storage = (elements.hostStorageInput.value || '').trim();
        if (storage) { hostRequirements.storage = storage; }
        if (elements.hostGpuInput.checked) { hostRequirements.gpu = true; }

        return {
            forwardPorts: parsePorts(elements.forwardPortsInput.value || ''),
            postCreateCommand: (elements.postCreateInput.value || '').trim(),
            postAttachCommand: (elements.postAttachInput.value || '').trim(),
            hostRequirements
        };
    }

    function ensureLockedFeaturesForCurrentProfile() {
        getLockedFeatureRefsForCurrentProfile().forEach((reference) => {
            if (state.selectedMap.has(reference)) {
                return;
            }
            const feature = state.catalog.find((item) => item.reference === reference);
            if (!feature) {
                return;
            }
            state.selectedMap.set(reference, {
                feature,
                options: cloneDefaultOptions(feature)
            });
        });
    }

    function applyPreset(presetKey, silent) {
        const presetRefs = PRESETS[presetKey] || [];
        state.profile = presetKey;
        state.selectedMap.clear();

        presetRefs.forEach((reference) => {
            const feature = state.catalog.find((item) => item.reference === reference);
            if (!feature) {
                return;
            }
            state.selectedMap.set(reference, {
                feature,
                options: cloneDefaultOptions(feature)
            });
        });

        ensureLockedFeaturesForCurrentProfile();

        const defaults = getProfileDefaults(state.profile);
        elements.forwardPortsInput.value = defaults.forwardPorts.join(', ');
        elements.postCreateInput.value = defaults.postCreateCommand;
        elements.postAttachInput.value = defaults.postAttachCommand;

        renderFoundation();
        filterCatalog();
        renderSelectedFeaturePills();
        renderConfigCards();
        updateSummary();
        saveDraft();

        if (!silent) {
            showMessage('success', presetKey === 'kubernetes'
                ? 'Kubernetes preset applied with cluster-ready defaults.'
                : 'Base preset applied with required foundation tooling.');
        }
    }

    function getMergedSettings() {
        const profileDefaults = getProfileDefaults(state.profile);
        const overrides = getAdvancedOverrides();

        return {
            forwardPorts: overrides.forwardPorts.length > 0 ? overrides.forwardPorts : profileDefaults.forwardPorts,
            portsAttributes: profileDefaults.portsAttributes,
            hostRequirements: Object.keys(overrides.hostRequirements).length > 0 ? overrides.hostRequirements : profileDefaults.hostRequirements,
            postCreateCommand: overrides.postCreateCommand || profileDefaults.postCreateCommand,
            postAttachCommand: overrides.postAttachCommand || profileDefaults.postAttachCommand,
            secrets: profileDefaults.secrets
        };
    }

    function buildPreviewObject() {
        ensureLockedFeaturesForCurrentProfile();
        const merged = getMergedSettings();
        const features = {};

        state.selectedMap.forEach((payload, reference) => {
            features[reference] = payload.options || {};
        });

        const previewObject = {
            name: (nameInput.value || '').trim() || 'My Devcontainer',
            image: (elements.baseImageInput.value || '').trim() || 'ubuntu:noble',
            features
        };

        if (merged.forwardPorts.length > 0) {
            previewObject.forwardPorts = merged.forwardPorts;
        }
        if (Object.keys(merged.portsAttributes).length > 0) {
            previewObject.portsAttributes = merged.portsAttributes;
        }
        if (Object.keys(merged.hostRequirements).length > 0) {
            previewObject.hostRequirements = merged.hostRequirements;
        }
        if (merged.postCreateCommand) {
            previewObject.postCreateCommand = merged.postCreateCommand;
        }
        if (merged.postAttachCommand) {
            previewObject.postAttachCommand = merged.postAttachCommand;
        }
        if (Object.keys(merged.secrets).length > 0) {
            previewObject.secrets = merged.secrets;
        }

        return previewObject;
    }

    function buildRequestPayload() {
        ensureLockedFeaturesForCurrentProfile();
        const merged = getMergedSettings();

        return {
            name: (nameInput.value || '').trim(),
            profile: state.profile,
            baseImage: (elements.baseImageInput.value || '').trim() || 'ubuntu:noble',
            includeGitIgnore: elements.includeGitIgnoreInput.checked,
            forwardPorts: merged.forwardPorts,
            portsAttributes: merged.portsAttributes,
            hostRequirements: merged.hostRequirements,
            postCreateCommand: merged.postCreateCommand,
            postAttachCommand: merged.postAttachCommand,
            secrets: merged.secrets,
            features: Array.from(state.selectedMap.entries()).map(([reference, payload]) => ({
                reference,
                options: payload.options || {}
            }))
        };
    }

    function renderFoundation() {
        elements.foundationList.innerHTML = '';

        getLockedFeatureRefsForCurrentProfile().forEach((reference) => {
            const feature = state.catalog.find((item) => item.reference === reference);
            if (!feature) {
                return;
            }

            const pill = document.createElement('div');
            pill.className = 'dcb-foundation-pill';
            pill.innerHTML = '<span>' + feature.displayName + '</span><small>Locked</small>';
            elements.foundationList.appendChild(pill);
        });

        elements.presetBaseButton.classList.toggle('is-selected', state.profile === 'base');
        elements.presetKubernetesButton.classList.toggle('is-selected', state.profile === 'kubernetes');
    }

    function updateQuickPickVisibility() {
        quickPickButtons.forEach((button) => {
            const reference = button.dataset.featureRef;
            const isLocked = isLockedFeatureForCurrentProfile(reference);
            const isSelected = state.selectedMap.has(reference);
            button.hidden = isLocked;
            button.disabled = isLocked;
            button.classList.toggle('is-active', isSelected);
        });
    }

function renderFeatureSelect() {
        updateQuickPickVisibility();

        const optional = state.filteredCatalog.filter((feature) => {
            return !isLockedFeatureForCurrentProfile(feature.reference) && !state.selectedMap.has(feature.reference);
        });

        elements.featureSelect.innerHTML = '';

        if (optional.length === 0) {
            const option = document.createElement('option');
            option.value = '';
            option.textContent = 'No optional add-ons match your filter';
            elements.featureSelect.appendChild(option);
            elements.addFeatureButton.disabled = true;
            return;
        }

        optional.forEach((feature) => {
            const option = document.createElement('option');
            option.value = feature.reference;
            option.textContent = feature.displayName + ' (' + feature.reference + ')';
            elements.featureSelect.appendChild(option);
        });

        elements.addFeatureButton.disabled = false;
    }

    function renderSelectedFeaturePills() {
        const optionalSelected = Array.from(state.selectedMap.entries()).filter(([reference]) => !isLockedFeatureForCurrentProfile(reference));

        elements.featureCount.textContent = optionalSelected.length + ' selected';
        elements.selectedFeaturePills.innerHTML = '';

        if (optionalSelected.length === 0) {
            const empty = document.createElement('div');
            empty.className = 'dcb-empty-state';
            empty.textContent = 'No optional add-ons selected yet. Foundation tooling is already included.';
            elements.selectedFeaturePills.appendChild(empty);
            return;
        }

        optionalSelected.forEach(([reference, payload]) => {
            const pill = document.createElement('div');
            pill.className = 'dcb-feature-pill';

            const label = document.createElement('span');
            label.textContent = payload.feature.displayName;

            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'dcb-pill-button';
            button.textContent = 'Remove';
            button.addEventListener('click', () => {
                if (isLockedFeatureForCurrentProfile(reference)) {
                    return;
                }
                state.selectedMap.delete(reference);
                ensureLockedFeaturesForCurrentProfile();
                filterCatalog();
                renderSelectedFeaturePills();
                renderConfigCards();
                updateSummary();
                updateQuickPickVisibility();
                saveDraft();
            });

            pill.appendChild(label);
            pill.appendChild(button);
            elements.selectedFeaturePills.appendChild(pill);
        });
    }

    function createOptionField(reference, optionName, optionMeta, payload) {
        const row = document.createElement('div');
        row.className = 'dcb-option-row';

        const label = document.createElement('label');
        label.className = 'dcb-feature-name';
        label.textContent = optionName;
        row.appendChild(label);

        const currentValue = payload.options[optionName];

        if (optionMeta.type === 'boolean') {
            const input = document.createElement('input');
            input.type = 'checkbox';
            input.className = 'dcb-option-checkbox';
            input.checked = Boolean(currentValue);
            input.addEventListener('change', () => {
                payload.options[optionName] = input.checked;
                updateSummary();
                saveDraft();
            });
            row.appendChild(input);
        } else {
            const input = document.createElement('input');
            input.className = 'dcb-option-input';
            input.type = optionMeta.type === 'number' || optionMeta.type === 'integer' ? 'number' : 'text';
            input.value = currentValue == null ? '' : String(currentValue);

            if (Array.isArray(optionMeta.proposals) && optionMeta.proposals.length > 0) {
                const datalist = document.createElement('datalist');
                const datalistId = 'dcb-' + btoa(reference + '-' + optionName).replace(/[^a-zA-Z0-9]/g, '');
                datalist.id = datalistId;
                optionMeta.proposals.forEach((proposal) => {
                    const option = document.createElement('option');
                    option.value = proposal;
                    datalist.appendChild(option);
                });
                input.setAttribute('list', datalistId);
                row.appendChild(datalist);
            }

            input.addEventListener('input', () => {
                payload.options[optionName] = sanitizeOptionValue(optionMeta, input.value);
                updateSummary();
                saveDraft();
            });
            row.appendChild(input);
        }

        const help = document.createElement('div');
        help.className = 'dcb-option-help';
        help.textContent = optionMeta.description || 'No additional description.';
        row.appendChild(help);

        return row;
    }

    function renderConfigCards() {
        const optionalSelected = Array.from(state.selectedMap.entries()).filter(([reference]) => !isLockedFeatureForCurrentProfile(reference));
        elements.configCards.innerHTML = '';

        if (optionalSelected.length === 0) {
            const empty = document.createElement('div');
            empty.className = 'dcb-empty-state';
            empty.textContent = 'Add an optional feature to configure its settings here.';
            elements.configCards.appendChild(empty);
            return;
        }

        optionalSelected.forEach(([reference, payload]) => {
            const card = document.createElement('article');
            card.className = 'dcb-config-card';

            const head = document.createElement('div');
            head.className = 'dcb-config-card-head';

            const copy = document.createElement('div');
            const title = document.createElement('h3');
            title.textContent = payload.feature.displayName;
            const meta = document.createElement('div');
            meta.className = 'dcb-feature-meta';
            meta.textContent = reference;
            copy.appendChild(title);
            copy.appendChild(meta);

            const removeButton = document.createElement('button');
            removeButton.type = 'button';
            removeButton.className = 'dcb-link-button dcb-link-button-secondary';
            removeButton.textContent = 'Remove';
            removeButton.addEventListener('click', () => {
                if (isLockedFeatureForCurrentProfile(reference)) {
                    return;
                }
                state.selectedMap.delete(reference);
                ensureLockedFeaturesForCurrentProfile();
                filterCatalog();
                renderSelectedFeaturePills();
                renderConfigCards();
                updateSummary();
                updateQuickPickVisibility();
                saveDraft();
            });

            head.appendChild(copy);
            head.appendChild(removeButton);
            card.appendChild(head);

            const options = Object.entries(payload.feature.options || {});
            if (options.length === 0) {
                const empty = document.createElement('div');
                empty.className = 'dcb-empty-state';
                empty.textContent = 'This feature has no configurable options in the curated catalog.';
                card.appendChild(empty);
            } else {
                const grid = document.createElement('div');
                grid.className = 'dcb-option-grid';
                options.forEach(([optionName, optionMeta]) => {
                    grid.appendChild(createOptionField(reference, optionName, optionMeta, payload));
                });
                card.appendChild(grid);
            }

            elements.configCards.appendChild(card);
        });
    }

    function filterCatalog() {
        const query = (elements.searchInput.value || '').trim().toLowerCase();
        const recommendation = RECOMMENDED_BY_PROFILE[state.profile] || [];

        state.filteredCatalog = state.catalog.filter((feature) => {
            if (isLockedFeatureForCurrentProfile(feature.reference)) {
                return false;
            }

            if (elements.recommendedOnlyInput.checked && !recommendation.includes(feature.reference)) {
                return false;
            }

            if (!query) {
                return true;
            }

            return (
                feature.displayName.toLowerCase().includes(query) ||
                feature.reference.toLowerCase().includes(query) ||
                feature.maintainer.toLowerCase().includes(query) ||
                feature.description.toLowerCase().includes(query)
            );
        });

        renderFeatureSelect();
    }

    function addFeatureByReference(reference) {
        const feature = state.catalog.find((item) => item.reference === reference);
        if (!feature) {
            showMessage('error', 'That add-on is not available in the current catalog.');
            return;
        }

        if (isLockedFeatureForCurrentProfile(reference)) {
            showMessage('error', 'That feature is part of the locked foundation and is already included.');
            return;
        }

        if (state.selectedMap.has(reference)) {
            return;
        }

        state.selectedMap.set(reference, {
            feature,
            options: cloneDefaultOptions(feature)
        });

        ensureLockedFeaturesForCurrentProfile();
        hideMessage();
        filterCatalog();
        renderSelectedFeaturePills();
        renderConfigCards();
        updateSummary();
        saveDraft();
    }

    function updateSummary() {
        ensureLockedFeaturesForCurrentProfile();

        const includedFiles = [
            { enabled: true, label: '.devcontainer/devcontainer.json' },
            { enabled: true, label: '.devcontainer/README.md' },
            { enabled: elements.includeGitIgnoreInput.checked, label: '.gitignore' }
        ].filter((item) => item.enabled);

        const featureEntries = Array.from(state.selectedMap.values());
        const addOnCount = featureEntries.filter((item) => !isLockedFeatureForCurrentProfile(item.feature.reference)).length;

        elements.summaryName.textContent = (nameInput.value || '').trim() || 'My Devcontainer';
        elements.summaryImage.textContent = 'Base image: ' + ((elements.baseImageInput.value || '').trim() || 'ubuntu:noble');
        elements.summaryCount.textContent = String(featureEntries.length);
        elements.generateMeta.textContent = 'Foundation + ' + addOnCount + ' add-ons selected';

        elements.summaryFiles.innerHTML = '';
        includedFiles.forEach((item) => {
            const div = document.createElement('div');
            div.className = 'dcb-summary-item';
            div.textContent = item.label;
            elements.summaryFiles.appendChild(div);
        });

        elements.summaryFeatureList.innerHTML = '';
        featureEntries.forEach((payload) => {
            const pill = document.createElement('span');
            pill.className = isLockedFeatureForCurrentProfile(payload.feature.reference)
                ? 'dcb-summary-feature-pill is-foundation'
                : 'dcb-summary-feature-pill';
            pill.textContent = payload.feature.displayName;
            elements.summaryFeatureList.appendChild(pill);
        });

        elements.preview.value = JSON.stringify(buildPreviewObject(), null, 2);
    }

    function saveDraft() {
        const payload = {
            name: nameInput.value,
            baseImage: elements.baseImageInput.value,
            includeGitIgnore: elements.includeGitIgnoreInput.checked,
            profile: state.profile,
            search: elements.searchInput.value,
            recommendedOnly: elements.recommendedOnlyInput.checked,
            forwardPorts: elements.forwardPortsInput.value,
            postCreateCommand: elements.postCreateInput.value,
            postAttachCommand: elements.postAttachInput.value,
            hostCpus: elements.hostCpusInput.value,
            hostMemory: elements.hostMemoryInput.value,
            hostStorage: elements.hostStorageInput.value,
            hostGpu: elements.hostGpuInput.checked,
            selectedReferences: Array.from(state.selectedMap.keys()).filter((reference) => !isLockedFeatureForCurrentProfile(reference)),
            selectedOptions: Array.from(state.selectedMap.entries())
                .filter(([reference]) => !isLockedFeatureForCurrentProfile(reference))
                .reduce((acc, [reference, payloadItem]) => {
                    acc[reference] = payloadItem.options || {};
                    return acc;
                }, {})
        };

        localStorage.setItem(DRAFT_STORAGE_KEY, JSON.stringify(payload));
    }

    function restoreDraft() {
        const raw = localStorage.getItem(DRAFT_STORAGE_KEY);
        if (!raw) {
            applyPreset('base', true);
            return;
        }

        try {
            const draft = JSON.parse(raw);
            state.profile = draft.profile === 'kubernetes' ? 'kubernetes' : 'base';
            applyPreset(state.profile, true);

            nameInput.value = draft.name || '';
            elements.baseImageInput.value = draft.baseImage || 'ubuntu:noble';
            elements.includeGitIgnoreInput.checked = draft.includeGitIgnore !== false;
            elements.searchInput.value = draft.search || '';
            elements.recommendedOnlyInput.checked = Boolean(draft.recommendedOnly);
            elements.forwardPortsInput.value = draft.forwardPorts || elements.forwardPortsInput.value;
            elements.postCreateInput.value = draft.postCreateCommand || elements.postCreateInput.value;
            elements.postAttachInput.value = draft.postAttachCommand || elements.postAttachInput.value;
            elements.hostCpusInput.value = draft.hostCpus || '2';
            elements.hostMemoryInput.value = draft.hostMemory || '4gb';
            elements.hostStorageInput.value = draft.hostStorage || '32gb';
            elements.hostGpuInput.checked = Boolean(draft.hostGpu);

            (draft.selectedReferences || []).forEach((reference) => {
                const feature = state.catalog.find((item) => item.reference === reference);
                if (!feature || isLockedFeatureForCurrentProfile(reference)) {
                    return;
                }

                const options = Object.assign(cloneDefaultOptions(feature), (draft.selectedOptions || {})[reference] || {});
                state.selectedMap.set(reference, {
                    feature,
                    options
                });
            });

            ensureLockedFeaturesForCurrentProfile();
            filterCatalog();
            renderSelectedFeaturePills();
            renderConfigCards();
            updateSummary();
        } catch (error) {
            applyPreset('base', true);
        }
    }

    async function downloadZip() {
        const payload = buildRequestPayload();
        if (!payload.name) {
            showMessage('error', 'Please provide a Demo name before generating the ZIP.');
            nameInput.focus();
            return;
        }

        hideMessage();
        elements.spinner.style.display = 'block';
        elements.downloadButton.disabled = true;
        elements.downloadButton.textContent = 'Generating...';

        try {
            const response = await fetch('/api/devcontainer/build', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(payload)
            });

            if (!response.ok) {
                const data = await response.json();
                throw new Error(data.detail || 'Failed to build devcontainer ZIP');
            }

            const blob = await response.blob();
            const contentDisposition = response.headers.get('Content-Disposition') || '';
            const match = /filename="?([^\";]+)"?/i.exec(contentDisposition);
            const filename = match ? match[1] : 'devcontainer-scaffold.zip';

            const url = URL.createObjectURL(blob);
            const link = document.createElement('a');
            link.href = url;
            link.download = filename;
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
            URL.revokeObjectURL(url);

            showMessage('success', 'Devcontainer scaffold ZIP generated and downloaded.');
        } catch (error) {
            showMessage('error', error.message || 'Failed to build devcontainer ZIP.');
        } finally {
            elements.spinner.style.display = 'none';
            elements.downloadButton.disabled = false;
            elements.downloadButton.textContent = 'Generate Devcontainer ZIP';
        }
    }

    function copyPreviewJson() {
        if (!elements.preview.value) {
            return;
        }

        navigator.clipboard.writeText(elements.preview.value)
            .then(() => {
                showMessage('success', 'Copied devcontainer.json to clipboard.');
            })
            .catch(() => {
                showMessage('error', 'Could not copy JSON automatically.');
            });
    }

    async function loadCatalog() {
        const response = await fetch('/api/devcontainer/features');
        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.detail || 'Failed to load devcontainer features');
        }

        state.catalog = data.features || [];
        state.filteredCatalog = [...state.catalog];
    }

    elements.searchInput.addEventListener('input', () => {
        filterCatalog();
        saveDraft();
    });

    elements.recommendedOnlyInput.addEventListener('change', () => {
        filterCatalog();
        saveDraft();
    });


    elements.addFeatureButton.addEventListener('click', () => {
        if (elements.featureSelect.value) {
            addFeatureByReference(elements.featureSelect.value);
        }
    });

    [nameInput, elements.baseImageInput, elements.forwardPortsInput, elements.postCreateInput, elements.postAttachInput, elements.hostCpusInput, elements.hostMemoryInput, elements.hostStorageInput].forEach((input) => {
        input.addEventListener('input', () => {
            updateSummary();
            saveDraft();
        });
    });

    elements.hostGpuInput.addEventListener('change', () => {
        updateSummary();
        saveDraft();
    });

    elements.includeGitIgnoreInput.addEventListener('change', () => {
        updateSummary();
        saveDraft();
    });

    quickPickButtons.forEach((button) => {
        button.addEventListener('click', () => {
            const reference = button.dataset.featureRef;
            if (button.classList.contains('is-active')) {
                const actualKey = Array.from(state.selectedMap.keys()).find(
                    (k) => k.toLowerCase() === reference.toLowerCase()
                );
                if (actualKey) {
                    state.selectedMap.delete(actualKey);
                }
                ensureLockedFeaturesForCurrentProfile();
                filterCatalog();
                renderSelectedFeaturePills();
                renderConfigCards();
                updateSummary();
                updateQuickPickVisibility();
                saveDraft();
            } else {
                addFeatureByReference(reference);
            }
        });
    });

    elements.presetBaseButton.addEventListener('click', () => {
        applyPreset('base');
    });

    elements.presetKubernetesButton.addEventListener('click', () => {
        applyPreset('kubernetes');
    });

    elements.resetButton.addEventListener('click', () => {
        applyPreset(state.profile);
    });

    elements.downloadButton.addEventListener('click', downloadZip);
    elements.copyJsonButton.addEventListener('click', copyPreviewJson);

    loadCatalog()
        .then(() => {
            restoreDraft();
            renderFoundation();
            filterCatalog();
            renderSelectedFeaturePills();
            renderConfigCards();
            updateSummary();
            hideMessage();
        })
        .catch((error) => {
            showMessage('error', 'Failed to load feature catalog: ' + error.message);
        });
})();
