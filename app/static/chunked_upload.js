(function () {
	const uploadRoot = document.querySelector('[data-chunked-upload-root]');
	if (!uploadRoot) {
		return;
	}

	const uploadForm = uploadRoot.querySelector('[data-upload-form]');
	const fileInput = uploadRoot.querySelector('[data-upload-file]');
	const submitButton = uploadRoot.querySelector('[data-upload-submit]');
	const overlay = document.getElementById('chunk-modal-overlay');
	const modal = document.getElementById('chunk-modal');
	if (!uploadForm || !fileInput || !submitButton || !overlay || !modal) {
		return;
	}

	const detailsFilename = modal.querySelector('[data-cu-filename]');
	const detailsSize = modal.querySelector('[data-cu-size]');
	const detailsProgress = modal.querySelector('[data-cu-progress]');
	const detailsChunks = modal.querySelector('[data-cu-chunks]');
	const detailsStatus = modal.querySelector('[data-cu-status]');
	const progressBar = modal.querySelector('[data-cu-progress-bar]');
	const pauseBtn = modal.querySelector('[data-cu-pause]');
	const resumeBtn = modal.querySelector('[data-cu-resume]');
	const cancelBtn = modal.querySelector('[data-cu-cancel]');
	const closeBtn = document.getElementById('chunk-modal-close');
	if (!detailsFilename || !detailsSize || !detailsProgress || !detailsChunks || !detailsStatus || !progressBar || !pauseBtn || !resumeBtn || !cancelBtn || !closeBtn) {
		return;
	}

	const thresholdBytes = Number(uploadRoot.dataset.chunkedThresholdBytes || 20 * 1024 * 1024);
	const defaultChunkSize = 5 * 1024 * 1024;
	const userId = (uploadRoot.dataset.userId || '').trim();
	const targetPath = uploadRoot.dataset.targetPath || '';
	const csrfToken = readCsrfToken();
	const retryDelaysMs = [500, 1000, 2000, 4000, 8000];
	const AUTO_CLOSE_ON_DONE = true;
	const DONE_CLOSE_DELAY_MS = 320;
	const DEBUG_UPLOAD_DONE = false;
	const storagePrefix = userId ? `cb_upload:${userId}:${targetPath}:` : `cb_upload:${targetPath}:`;
	let lastFocusedElement = null;

	const detailsExtra = ensureExtraDetails();
	const resumePrompt = ensureResumePrompt();

	const state = {
		file: null,
		uploadId: null,
		totalChunks: 0,
		uploadedCount: 0,
		uploadedBytes: 0,
		status: 'idle',
		controller: null,
		isUploading: false,
		overwrite: false,
		activeStorageKey: null,
		speedSamples: [],
		retryText: '-',
		lastError: '-',
		cancelRequested: false,
		cancelInFlight: false,
	};

	function ensureExtraDetails() {
		const container = document.createElement('div');
		container.className = 'muted';
		container.style.marginTop = '8px';
		container.innerHTML = '<p><b>Speed:</b> <span data-cu-speed>-</span></p><p><b>ETA:</b> <span data-cu-eta>-</span></p><p><b>Current chunk:</b> <span data-cu-current>-</span></p><p><b>Retry:</b> <span data-cu-retry>-</span></p><p><b>Last error:</b> <span data-cu-error>-</span></p>';
		const actions = modal.querySelector('.chunked-upload-actions');
		if (actions) {
			modal.insertBefore(container, actions);
		} else {
			modal.appendChild(container);
		}
		return {
			speed: container.querySelector('[data-cu-speed]'),
			eta: container.querySelector('[data-cu-eta]'),
			current: container.querySelector('[data-cu-current]'),
			retry: container.querySelector('[data-cu-retry]'),
			error: container.querySelector('[data-cu-error]'),
		};
	}

	function ensureResumePrompt() {
		const prompt = document.createElement('div');
		prompt.className = 'notice hidden';
		prompt.setAttribute('data-cu-resume-prompt', '1');
		prompt.innerHTML = '<span data-cu-resume-text></span> <button type="button" data-cu-resume-accept>Resume</button> <button type="button" data-cu-resume-dismiss>Dismiss</button>';
		uploadRoot.insertBefore(prompt, uploadForm);
		return {
			root: prompt,
			text: prompt.querySelector('[data-cu-resume-text]'),
			resume: prompt.querySelector('[data-cu-resume-accept]'),
			dismiss: prompt.querySelector('[data-cu-resume-dismiss]'),
			pendingState: null,
		};
	}

	function readCsrfToken() {
		const tokenInput = uploadForm.querySelector('input[name="csrf_token"]') || document.querySelector('input[name="csrf_token"]');
		if (tokenInput && tokenInput.value) {
			return tokenInput.value;
		}
		const meta = document.querySelector('meta[name="csrf-token"]');
		return meta ? meta.getAttribute('content') : '';
	}

	function storageKeyForFile(file) {
		const parts = [
			'cb_upload',
		];
		if (userId) {
			parts.push(userId);
		}
		parts.push(targetPath);
		parts.push(file.name);
		parts.push(String(file.size));
		parts.push(String(file.lastModified));
		return parts.join(':');
	}

	function hasValidFile(file) {
		return !!file && Number.isFinite(file.size) && file.size > 0;
	}

	function formatBytes(bytes) {
		if (!Number.isFinite(bytes) || bytes <= 0) {
			return '0 B';
		}
		const units = ['B', 'KB', 'MB', 'GB'];
		let value = bytes;
		let idx = 0;
		while (value >= 1024 && idx < units.length - 1) {
			value /= 1024;
			idx += 1;
		}
		return idx === 0 ? `${Math.round(value)} ${units[idx]}` : `${value.toFixed(1)} ${units[idx]}`;
	}

	function formatSpeed(bytesPerSecond) {
		if (!Number.isFinite(bytesPerSecond) || bytesPerSecond <= 0) {
			return '-';
		}
		return `${(bytesPerSecond / (1024 * 1024)).toFixed(2)} MB/s`;
	}

	function formatEta(seconds) {
		if (!Number.isFinite(seconds) || seconds <= 0) {
			return '-';
		}
		if (seconds < 60) {
			return `${Math.ceil(seconds)}s`;
		}
		return `${Math.ceil(seconds / 60)}m`;
	}

	function setStatus(message) {
		detailsStatus.textContent = message;
	}

	function setExtraDetails() {
		detailsExtra.retry.textContent = state.retryText;
		detailsExtra.error.textContent = state.lastError;
		const speed = computeSpeed();
		detailsExtra.speed.textContent = formatSpeed(speed);
		const remainingBytes = Math.max((state.file ? state.file.size : 0) - state.uploadedBytes, 0);
		detailsExtra.eta.textContent = speed > 0 ? formatEta(remainingBytes / speed) : '-';
		detailsExtra.current.textContent = state.totalChunks > 0 ? `${Math.min(state.uploadedCount + 1, state.totalChunks)} / ${state.totalChunks}` : '-';
	}

	function resetModalUI() {
		detailsFilename.textContent = '-';
		detailsSize.textContent = '0 B';
		detailsProgress.textContent = '0%';
		detailsChunks.textContent = '0 / 0';
		progressBar.style.width = '0%';
		state.retryText = '-';
		state.lastError = '-';
		setStatus('Waiting...');
		setButtonsForStatus('idle');
		setExtraDetails();
	}

	function isModalVisible() {
		return !overlay.classList.contains('hidden');
	}

	function hideModal(options) {
		const opts = Object.assign({ reset: false }, options || {});
		overlay.classList.add('hidden');
		document.body.classList.remove('modal-open');
		if (lastFocusedElement && typeof lastFocusedElement.focus === 'function') {
			lastFocusedElement.focus();
		}
		lastFocusedElement = null;
		if (opts.reset) {
			resetModalUI();
		}
	}

	function showModal(file) {
		if (!hasValidFile(file)) {
			hideModal({ reset: true });
			return;
		}
		lastFocusedElement = document.activeElement;
		overlay.classList.remove('hidden');
		document.body.classList.add('modal-open');
		if (typeof closeBtn.focus === 'function') {
			closeBtn.focus();
		}
	}

	function buildFilesRedirectUrl() {
		const url = new URL(window.location.href);
		const params = new URLSearchParams(url.search);
		params.set('uploaded', '1');
		const pathValue = params.get('path');
		if (pathValue === null) {
			params.set('path', targetPath);
		}
		return `${url.pathname}?${params.toString()}`;
	}

	function onDoneUiSettledNavigate() {
		const redirectUrl = buildFilesRedirectUrl();
		if (DEBUG_UPLOAD_DONE) {
			console.debug('UPLOAD_DONE navigating to', redirectUrl);
		}
		requestAnimationFrame(function () {
			void modal.offsetHeight;
			window.setTimeout(function () {
				hideModal({ reset: false });
				window.location.assign(redirectUrl);
			}, DONE_CLOSE_DELAY_MS);
		});
	}

	function setSubmitDisabled(disabled) {
		submitButton.disabled = disabled;
	}

	function setButtonsForStatus(status) {
		const isPaused = status === 'paused';
		const isCancelable = status === 'uploading' || status === 'resuming' || status === 'preparing' || status === 'completing';
		const isActive = isCancelable;
		pauseBtn.disabled = !isActive;
		resumeBtn.disabled = !(isPaused || status === 'error');
		cancelBtn.disabled = !isCancelable || state.cancelInFlight;
	}

	function updateProgress(uploadedCount, totalChunks) {
		const safeTotal = totalChunks > 0 ? totalChunks : 1;
		const percent = Math.floor((uploadedCount / safeTotal) * 100);
		detailsProgress.textContent = `${percent}%`;
		detailsChunks.textContent = `${Math.min(uploadedCount, safeTotal)} / ${safeTotal}`;
		progressBar.style.width = `${percent}%`;
		state.uploadedCount = uploadedCount;
		setExtraDetails();
	}

	function recordSpeedSample(bytesUploaded) {
		state.speedSamples.push({ ts: Date.now(), bytes: bytesUploaded });
		const cutoff = Date.now() - 5000;
		state.speedSamples = state.speedSamples.filter(function (sample) {
			return sample.ts >= cutoff;
		});
	}

	function computeSpeed() {
		if (state.speedSamples.length < 2) {
			return 0;
		}
		const first = state.speedSamples[0];
		const last = state.speedSamples[state.speedSamples.length - 1];
		const elapsedSeconds = (last.ts - first.ts) / 1000;
		if (elapsedSeconds <= 0) {
			return 0;
		}
		const bytesDelta = last.bytes - first.bytes;
		return bytesDelta > 0 ? bytesDelta / elapsedSeconds : 0;
	}

	function saveUploadState(file, payload) {
		const key = storageKeyForFile(file);
		const data = {
			uploadId: payload.upload_id,
			path: targetPath,
			filename: file.name,
			size: file.size,
			lastModified: file.lastModified,
			chunkSize: payload.chunk_size || defaultChunkSize,
			totalChunks: payload.total_chunks || Math.ceil(file.size / defaultChunkSize),
			overwrite: !!payload.overwrite,
			createdAt: Date.now(),
		};
		localStorage.setItem(key, JSON.stringify(data));
		state.activeStorageKey = key;
		return data;
	}

	function readUploadStateForFile(file) {
		const key = storageKeyForFile(file);
		const raw = localStorage.getItem(key);
		if (!raw) {
			return null;
		}
		try {
			const parsed = JSON.parse(raw);
			if (!parsed || !parsed.uploadId) {
				localStorage.removeItem(key);
				return null;
			}
			state.activeStorageKey = key;
			return parsed;
		} catch (_err) {
			localStorage.removeItem(key);
			return null;
		}
	}

	function clearStoredState(file) {
		if (file) {
			localStorage.removeItem(storageKeyForFile(file));
		}
		if (state.activeStorageKey) {
			localStorage.removeItem(state.activeStorageKey);
			state.activeStorageKey = null;
		}
	}

	function parseErrorMessage(err) {
		if (!err) {
			return 'Unknown error';
		}
		if (err.payload && typeof err.payload.error === 'string' && err.payload.error) {
			return err.payload.error;
		}
		if (err.message) {
			return err.message;
		}
		return 'Unexpected error';
	}

	async function jsonRequest(url, options) {
		const opts = Object.assign({}, options || {});
		opts.headers = Object.assign({}, opts.headers || {});
		if (opts.method && opts.method.toUpperCase() === 'POST' && csrfToken) {
			opts.headers['X-CSRFToken'] = csrfToken;
		}
		if (!opts.signal && state.controller) {
			opts.signal = state.controller.signal;
		}
		const response = await fetch(url, opts);
		let payload = {};
		try {
			payload = await response.json();
		} catch (_err) {
			payload = {};
		}
		if (!response.ok) {
			const err = new Error(payload.error || `Request failed (${response.status})`);
			err.status = response.status;
			err.payload = payload;
			throw err;
		}
		return payload;
	}

	async function checkExistingFile(file) {
		const params = new URLSearchParams({
			path: targetPath,
			filename: file.name,
		});
		return jsonRequest(`/api/uploads/exists?${params.toString()}`, {
			method: 'GET'
		});
	}

	async function fetchStatus(uploadId) {
		return jsonRequest(`/api/uploads/${encodeURIComponent(uploadId)}/status`, {
			method: 'GET'
		});
	}

	async function initUpload(file, overwrite) {
		const totalChunks = Math.ceil(file.size / defaultChunkSize);
		const payload = await jsonRequest('/api/uploads/init', {
			method: 'POST',
			headers: {
				'Content-Type': 'application/json'
			},
			body: JSON.stringify({
				filename: file.name,
				total_size: file.size,
				chunk_size: defaultChunkSize,
				total_chunks: totalChunks,
				target_path: targetPath,
				overwrite: !!overwrite,
			})
		});
		payload.overwrite = !!overwrite;
		saveUploadState(file, payload);
		return payload;
	}

	function getMissingChunksFromStatus(statusPayload, fallbackTotal) {
		if (Array.isArray(statusPayload.missing_chunks)) {
			return statusPayload.missing_chunks.slice();
		}
		const totalChunks = Number(statusPayload.total_chunks || fallbackTotal || 0);
		if (!Array.isArray(statusPayload.uploaded_chunks)) {
			return [];
		}
		const uploadedSet = new Set(statusPayload.uploaded_chunks);
		const missing = [];
		for (let idx = 0; idx < totalChunks; idx += 1) {
			if (!uploadedSet.has(idx)) {
				missing.push(idx);
			}
		}
		return missing;
	}

	function isRetryable(statusCode) {
		return statusCode === 429 || statusCode >= 500;
	}

	function delay(ms, signal) {
		return new Promise(function (resolve, reject) {
			const timer = window.setTimeout(function () {
				cleanup();
				resolve();
			}, ms);
			function onAbort() {
				cleanup();
				const abortError = new Error('Aborted');
				abortError.name = 'AbortError';
				reject(abortError);
			}
			function cleanup() {
				window.clearTimeout(timer);
				if (signal) {
					signal.removeEventListener('abort', onAbort);
				}
			}
			if (signal) {
				if (signal.aborted) {
					onAbort();
					return;
				}
				signal.addEventListener('abort', onAbort);
			}
		});
	}

	async function uploadChunkWithRetry(uploadId, chunkIndex, chunkBlob) {
		for (let attempt = 1; attempt <= retryDelaysMs.length; attempt += 1) {
			if (state.cancelRequested) {
				const abortError = new Error('Aborted');
				abortError.name = 'AbortError';
				throw abortError;
			}
			try {
				const response = await fetch(`/api/uploads/${encodeURIComponent(uploadId)}/chunk`, {
					method: 'POST',
					headers: {
						'Content-Type': 'application/octet-stream',
						'X-CSRFToken': csrfToken,
						'X-Chunk-Index': String(chunkIndex),
						'X-Chunk-Size': String(chunkBlob.size),
					},
					body: chunkBlob,
					signal: state.controller ? state.controller.signal : undefined,
				});
				let payload = {};
				try {
					payload = await response.json();
				} catch (_err) {
					payload = {};
				}
				if (response.ok) {
					state.retryText = '-';
					state.lastError = '-';
					return payload;
				}
				const error = new Error(payload.error || `Chunk upload failed (${response.status})`);
				error.status = response.status;
				error.payload = payload;
				if (isRetryable(response.status) && attempt < retryDelaysMs.length && !state.cancelRequested) {
					state.retryText = `Retry ${attempt + 1}/${retryDelaysMs.length}...`;
					state.lastError = parseErrorMessage(error);
					setExtraDetails();
					await delay(retryDelaysMs[attempt - 1], state.controller ? state.controller.signal : null);
					continue;
				}
				throw error;
			} catch (error) {
				if (error && error.name === 'AbortError') {
					throw error;
				}
				if (isRetryable(error.status || 0) && attempt < retryDelaysMs.length && !state.cancelRequested) {
					state.retryText = `Retry ${attempt + 1}/${retryDelaysMs.length}...`;
					state.lastError = parseErrorMessage(error);
					setExtraDetails();
					await delay(retryDelaysMs[attempt - 1], state.controller ? state.controller.signal : null);
					continue;
				}
				throw error;
			}
		}
		throw new Error('Chunk retries exhausted');
	}

	async function completeUpload(uploadId) {
		return jsonRequest(`/api/uploads/${encodeURIComponent(uploadId)}/complete`, {
			method: 'POST',
			headers: {
				'Content-Type': 'application/json'
			},
			body: JSON.stringify({}),
		});
	}

	function markPaused() {
		state.status = 'paused';
		setButtonsForStatus('paused');
		setSubmitDisabled(false);
		setStatus('Upload paused. You can resume.');
	}

	function markCanceled(message) {
		state.status = 'canceled';
		state.cancelInFlight = false;
		state.cancelRequested = true;
		setButtonsForStatus('canceled');
		setSubmitDisabled(false);
		setStatus(message || 'Upload canceled. Start a new upload to continue.');
		clearStoredState(state.file);
		hideResumePrompt();
	}

	function markDone(message) {
		state.status = 'done';
		setButtonsForStatus('done');
		setSubmitDisabled(false);
		setStatus(message);
		clearStoredState(state.file);
		if (AUTO_CLOSE_ON_DONE) {
			onDoneUiSettledNavigate();
		}
	}

	function clearSelectionAndState() {
		clearStoredState(state.file);
		fileInput.value = '';
		state.file = null;
		state.uploadId = null;
		state.totalChunks = 0;
		state.uploadedCount = 0;
		state.uploadedBytes = 0;
		state.controller = null;
		state.speedSamples = [];
		state.cancelRequested = false;
		state.cancelInFlight = false;
	}

	function showResumePrompt(savedState, message) {
		resumePrompt.pendingState = savedState;
		resumePrompt.text.textContent = message || `Resume previous upload for ${savedState.filename}?`;
		resumePrompt.root.classList.remove('hidden');
	}

	function hideResumePrompt() {
		resumePrompt.pendingState = null;
		resumePrompt.root.classList.add('hidden');
	}

	function findSavedStateForCurrentPath() {
		let latest = null;
		for (let index = 0; index < localStorage.length; index += 1) {
			const key = localStorage.key(index);
			if (!key || !key.startsWith(storagePrefix)) {
				continue;
			}
			const raw = localStorage.getItem(key);
			if (!raw) {
				continue;
			}
			try {
				const parsed = JSON.parse(raw);
				if (!parsed || !parsed.uploadId || !parsed.filename) {
					continue;
				}
				if (!latest || Number(parsed.createdAt || 0) > Number(latest.createdAt || 0)) {
					latest = parsed;
				}
			} catch (_err) {
				localStorage.removeItem(key);
			}
		}
		return latest;
	}

	function maybePromptResumeForFile(file) {
		if (!hasValidFile(file) || file.size <= thresholdBytes) {
			hideResumePrompt();
			return;
		}
		const saved = readUploadStateForFile(file);
		if (saved && saved.uploadId) {
			showResumePrompt(saved, `Resume previous upload for ${saved.filename}?`);
			setStatus('Resume is available for this file.');
			return;
		}
		hideResumePrompt();
	}

	async function runChunkedUpload(file, options) {
		const opts = Object.assign({ forceResume: false }, options || {});
		if (state.isUploading || !hasValidFile(file)) {
			return;
		}
		state.isUploading = true;
		state.file = file;
		state.uploadedBytes = 0;
		state.speedSamples = [];
		state.lastError = '-';
		state.retryText = '-';
		state.cancelRequested = false;
		state.cancelInFlight = false;
		state.controller = new AbortController();
		recordSpeedSample(0);
		setSubmitDisabled(true);
		showModal(file);
		detailsFilename.textContent = file.name;
		detailsSize.textContent = formatBytes(file.size);

		try {
			let saved = readUploadStateForFile(file);
			let uploadId = saved ? saved.uploadId : null;
			let overwrite = !!(saved && saved.overwrite);

			if (!uploadId) {
				state.status = 'preparing';
				setButtonsForStatus('preparing');
				setStatus('Preparing upload...');
				const existsPayload = await checkExistingFile(file);
				if (existsPayload && existsPayload.exists) {
					overwrite = window.confirm(`A file with the same name already exists in this folder: ${file.name}. Overwrite?`);
					if (!overwrite) {
						state.status = 'error';
						setButtonsForStatus('error');
						setSubmitDisabled(false);
						setStatus('Upload canceled. Existing file was not overwritten.');
						return;
					}
				}
				const initPayload = await initUpload(file, overwrite);
				saved = readUploadStateForFile(file);
				uploadId = initPayload.upload_id;
			}

			state.uploadId = uploadId;
			state.overwrite = overwrite;
			state.status = opts.forceResume ? 'resuming' : 'uploading';
			setButtonsForStatus(state.status);
			setStatus(opts.forceResume ? 'Resuming upload...' : 'Uploading chunks...');

			const statusPayload = await fetchStatus(uploadId);
			if (statusPayload.status === 'completed') {
				markDone('Upload already completed.');
				return;
			}
			if (statusPayload.status === 'canceled') {
				clearStoredState(file);
				markCanceled('This upload was canceled and cannot be resumed. Start a new upload.');
				return;
			}

			const totalChunks = Number(statusPayload.total_chunks || (saved && saved.totalChunks) || Math.ceil(file.size / defaultChunkSize));
			state.totalChunks = totalChunks;
			const missingChunks = getMissingChunksFromStatus(statusPayload, totalChunks);
			const uploadedCount = Math.max(totalChunks - missingChunks.length, 0);
			const chunkSize = Number(statusPayload.chunk_size || (saved && saved.chunkSize) || defaultChunkSize);
			state.uploadedBytes = Math.min(uploadedCount * chunkSize, file.size);
			recordSpeedSample(state.uploadedBytes);
			updateProgress(uploadedCount, totalChunks);

			for (const chunkIndex of missingChunks) {
				if ((state.status !== 'uploading' && state.status !== 'resuming') || state.cancelRequested) {
					break;
				}
				state.retryText = '-';
				setExtraDetails();
				const start = chunkIndex * chunkSize;
				const end = Math.min(start + chunkSize, file.size);
				const chunkBlob = file.slice(start, end);
				await uploadChunkWithRetry(uploadId, chunkIndex, chunkBlob);
				state.uploadedBytes += chunkBlob.size;
				recordSpeedSample(state.uploadedBytes);
				updateProgress(state.uploadedCount + 1, totalChunks);
			}

			if ((state.status !== 'uploading' && state.status !== 'resuming') || state.cancelRequested) {
				return;
			}

			state.status = 'completing';
			setButtonsForStatus('completing');
			setStatus('Completing upload...');
			const completePayload = await completeUpload(uploadId);
			if (completePayload.duplicate) {
				markDone('File already exists. Upload skipped.');
				return;
			}
			updateProgress(totalChunks, totalChunks);
			markDone('Upload complete.');
		} catch (err) {
			if (err && err.name === 'AbortError') {
				if (state.cancelRequested) {
					markCanceled('Upload canceled. Start a new upload to continue.');
					return;
				}
				markPaused();
				return;
			}
			state.status = 'error';
			setButtonsForStatus('error');
			setSubmitDisabled(false);
			state.lastError = parseErrorMessage(err);
			setExtraDetails();
			if (err && [400, 401, 403, 409].includes(err.status)) {
				setStatus(`Upload failed: ${parseErrorMessage(err)}`);
			} else {
				setStatus('Upload interrupted. You can resume.');
			}
		} finally {
			state.isUploading = false;
			state.controller = null;
		}
	}

	function pauseUpload() {
		if (state.controller) {
			state.controller.abort();
		}
	}

	function resumeUpload() {
		const file = fileInput.files && fileInput.files[0] ? fileInput.files[0] : state.file;
		if (!file) {
			setStatus('Select the same file to resume upload.');
			return;
		}
		hideResumePrompt();
		runChunkedUpload(file, { forceResume: true });
	}

	async function dismissResumeState() {
		const file = fileInput.files && fileInput.files[0] ? fileInput.files[0] : null;
		if (file) {
			clearStoredState(file);
		} else if (resumePrompt.pendingState) {
			const pending = resumePrompt.pendingState;
			const fakeFile = {
				name: pending.filename,
				size: pending.size,
				lastModified: pending.lastModified,
			};
			clearStoredState(fakeFile);
		}
		hideResumePrompt();
	}

	async function cancelUpload() {
		if (state.cancelInFlight || state.status === 'done' || state.status === 'canceled') {
			return;
		}
		if (!state.uploadId) {
			markCanceled('Upload canceled. Start a new upload to continue.');
			return;
		}
		if (!window.confirm('Cancel this upload? Uploaded chunks will be deleted and cannot be resumed.')) {
			return;
		}

		const cancelUploadId = state.uploadId;
		state.cancelRequested = true;
		state.cancelInFlight = true;
		setButtonsForStatus(state.status);
		if (state.controller) {
			state.controller.abort();
		}

		try {
			await jsonRequest(`/api/uploads/${encodeURIComponent(cancelUploadId)}/cancel`, {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({}),
				signal: null,
			});
			clearStoredState(state.file);
			markCanceled('Upload canceled. Start a new upload to continue.');
		} catch (err) {
			clearStoredState(state.file);
			state.cancelInFlight = false;
			state.status = 'error';
			setButtonsForStatus('error');
			setSubmitDisabled(false);
			state.lastError = `Cancel failed: ${parseErrorMessage(err)}`;
			setExtraDetails();
			setStatus('Upload canceled locally, but server cleanup failed. Start a new upload.');
		}
	}

	fileInput.addEventListener('change', function () {
		const file = fileInput.files && fileInput.files[0] ? fileInput.files[0] : null;
		if (!file || file.size <= thresholdBytes) {
			hideModal({ reset: true });
			hideResumePrompt();
			return;
		}
		detailsFilename.textContent = file.name;
		detailsSize.textContent = formatBytes(file.size);
		updateProgress(0, Math.ceil(file.size / defaultChunkSize));
		setStatus('Ready to upload. Press upload to begin.');
		setButtonsForStatus('preparing');
		resumeBtn.disabled = true;
		maybePromptResumeForFile(file);
	});

	uploadForm.addEventListener('submit', function (event) {
		const file = fileInput.files && fileInput.files[0] ? fileInput.files[0] : null;
		if (!file || file.size <= thresholdBytes) {
			return;
		}
		event.preventDefault();
		hideResumePrompt();
		runChunkedUpload(file, { forceResume: false });
	});

	submitButton.addEventListener('click', function () {
		if (state.isUploading) {
			return;
		}
		const file = fileInput.files && fileInput.files[0] ? fileInput.files[0] : null;
		if (!file || file.size <= thresholdBytes) {
			uploadForm.requestSubmit();
			return;
		}
		hideResumePrompt();
		runChunkedUpload(file, { forceResume: false });
	});

	pauseBtn.addEventListener('click', pauseUpload);
	resumeBtn.addEventListener('click', resumeUpload);
	cancelBtn.addEventListener('click', cancelUpload);
	closeBtn.addEventListener('click', function () {
		hideModal({ reset: false });
	});
	overlay.addEventListener('click', function (event) {
		if (event.target === overlay) {
			hideModal({ reset: false });
		}
	});
	document.addEventListener('keydown', function (event) {
		if (event.key === 'Escape' && isModalVisible()) {
			hideModal({ reset: false });
		}
	});

	resumePrompt.resume.addEventListener('click', function () {
		resumeUpload();
	});
	resumePrompt.dismiss.addEventListener('click', function () {
		dismissResumeState();
	});

	document.addEventListener('DOMContentLoaded', function () {
		hideModal({ reset: true });
		const saved = findSavedStateForCurrentPath();
		if (saved) {
			showResumePrompt(saved, `Resume previous upload for ${saved.filename}?`);
			setStatus('A previous upload can be resumed after selecting the same file.');
		}
	});

	hideModal({ reset: true });
})();
