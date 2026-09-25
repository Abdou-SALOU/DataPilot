(() => {
  const t = (text) => (window.DP_I18N && window.DP_I18N[text]) || text;
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const header = document.querySelector('[data-header]');

  let hashTarget = null;
  try {
    hashTarget = location.hash ? document.getElementById(decodeURIComponent(location.hash.slice(1))) : null;
  } catch (_error) {
    hashTarget = null;
  }
  hashTarget?.closest('details')?.setAttribute('open', '');

  const syncHeader = () => header?.classList.toggle('is-scrolled', window.scrollY > 12);
  syncHeader();
  window.addEventListener('scroll', syncHeader, { passive: true });

  const revealItems = [...document.querySelectorAll('[data-reveal]')];
  if (!reducedMotion && 'IntersectionObserver' in window) {
    revealItems.forEach((item) => item.classList.add('reveal-pending'));
    const revealObserver = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        entry.target.classList.add('is-visible');
        revealObserver.unobserve(entry.target);
      });
    }, { threshold: 0.1, rootMargin: '0px 0px -7% 0px' });

    revealItems.forEach((item) => {
      if (item.dataset.reveal === 'hero' || (location.hash && item.matches(location.hash))) {
        requestAnimationFrame(() => item.classList.add('is-visible'));
      } else {
        revealObserver.observe(item);
      }
    });
  } else {
    revealItems.forEach((item) => item.classList.add('is-visible'));
  }

  const fileInput = document.getElementById('dataset');
  const dropzone = document.querySelector('.dropzone');
  const fileName = document.getElementById('fileName');
  const syncSelectedFile = () => {
    const selected = fileInput?.files?.[0];
    if (fileName) fileName.textContent = selected?.name || t('Aucun fichier sélectionné');
    dropzone?.classList.toggle('has-file', Boolean(selected));
  };
  fileInput?.addEventListener('change', syncSelectedFile);

  ['dragenter', 'dragover'].forEach((eventName) => dropzone?.addEventListener(eventName, (event) => {
    event.preventDefault();
    dropzone.classList.add('is-dragging');
  }));
  ['dragleave', 'dragend'].forEach((eventName) => dropzone?.addEventListener(eventName, () => dropzone.classList.remove('is-dragging')));
  dropzone?.addEventListener('drop', (event) => {
    event.preventDefault();
    dropzone.classList.remove('is-dragging');
    if (!event.dataTransfer?.files?.length || !fileInput) return;
    fileInput.files = event.dataTransfer.files;
    syncSelectedFile();
  });

  const setLoadingForm = (form, loading) => {
    const button = form.querySelector('[data-submit-button]') || form.querySelector('button[type="submit"]');
    const state = form.querySelector('.loading-state');
    if (!loading) {
      form.removeAttribute('aria-busy');
      if (button) {
        if (button.dataset.disabledBeforeLoading !== undefined) {
          button.disabled = button.dataset.disabledBeforeLoading === 'true';
          delete button.dataset.disabledBeforeLoading;
        } else if (!button.hasAttribute('data-ready-button')) {
          button.disabled = false;
        }
        if (button.dataset.originalHtml) button.innerHTML = button.dataset.originalHtml;
      }
      state?.setAttribute('hidden', '');
      return;
    }

    form.setAttribute('aria-busy', 'true');
    if (button) {
      button.dataset.disabledBeforeLoading = String(button.disabled);
      button.dataset.originalHtml ||= button.innerHTML;
      button.disabled = true;
      button.innerHTML = `<span class="button-spinner" aria-hidden="true"></span>${form.dataset.loadingText || t('Traitement en cours…')}`;
    }
    state?.removeAttribute('hidden');
  };

  window.addEventListener('pageshow', () => document.querySelectorAll('[data-loading-form]').forEach((form) => setLoadingForm(form, false)));

  document.querySelectorAll('[data-export-download]').forEach((link) => {
    link.addEventListener('click', () => {
      if (link.dataset.exportPending === 'true') return;
      link.dataset.exportPending = 'true';
      link.dataset.exportOriginalText ||= link.textContent;
      link.textContent = link.dataset.exportLabel || t('Préparation du téléchargement…');
      link.classList.add('is-preparing-download');
      link.setAttribute('aria-busy', 'true');
      const resetAfter = Number.parseInt(link.dataset.exportTimeout || '90000', 10);
      window.setTimeout(() => {
        link.dataset.exportPending = 'false';
        link.textContent = link.dataset.exportOriginalText || link.textContent;
        link.classList.remove('is-preparing-download');
        link.removeAttribute('aria-busy');
      }, Number.isFinite(resetAfter) ? resetAfter : 90000);
    });
  });

  const initializeTranslation = (root = document, { focus = false } = {}) => {
    const translationBuilder = root.matches?.('[data-translation-builder]')
      ? root
      : root.querySelector?.('[data-translation-builder]');
    if (translationBuilder && translationBuilder.dataset.translationReady !== 'true') {
      translationBuilder.dataset.translationReady = 'true';
      const column = translationBuilder.querySelector('[name="column"]');
      const displayName = translationBuilder.querySelector('[name="display_name"]');
      const includeValues = translationBuilder.querySelector('[data-include-values]');
      const valuesOption = translationBuilder.querySelector('[data-rename-values-option]');
      const help = translationBuilder.querySelector('[data-translation-help]');
      const submitButton = translationBuilder.querySelector('[data-ready-button]');
      const humanizeColumnName = (value) => {
        const upperWords = new Set(['id', 'mad', 'sku', 'tva', 'ht', 'ttc']);
        const words = String(value || '').split(/[_\-\s]+/).filter(Boolean);
        return words.map((word, index) => {
          const normalized = word.toLocaleLowerCase();
          if (upperWords.has(normalized)) return normalized.toLocaleUpperCase();
          return index === 0
            ? normalized.charAt(0).toLocaleUpperCase() + normalized.slice(1)
            : normalized;
        }).join(' ');
      };
      const syncTranslationChoice = ({ suggest = false } = {}) => {
        const selectedOption = column?.selectedOptions?.[0];
        const selected = Boolean(selectedOption?.value);
        const canTranslateValues = selectedOption?.dataset.canValues === 'true';
        if (includeValues) {
          includeValues.disabled = !canTranslateValues;
          if (!canTranslateValues) includeValues.checked = false;
        }
        if (valuesOption) valuesOption.hidden = !selected || !canTranslateValues;
        if (help) {
          help.hidden = !selected || canTranslateValues;
          help.textContent = selectedOption?.dataset.reason || '';
        }
        if (displayName) {
          const suggestedName = selectedOption?.dataset.suggestedName
            || humanizeColumnName(selectedOption?.value);
          if (suggest && selected) displayName.value = suggestedName;
          displayName.placeholder = selected
            ? suggestedName
            : t('Proposé automatiquement');
        }
        if (submitButton) submitButton.disabled = !selected || !displayName?.value.trim();
        translationBuilder.classList.toggle('has-column', selected);
      };
      column?.addEventListener('change', () => syncTranslationChoice({ suggest: true }));
      displayName?.addEventListener('input', () => syncTranslationChoice());
      syncTranslationChoice();
    }

    const translationPreview = root.matches?.('#translation-preview')
      ? root
      : root.querySelector?.('#translation-preview');
    if (translationPreview && translationPreview.dataset.translationReady !== 'true') {
      translationPreview.dataset.translationReady = 'true';
      const translatedInputs = [...translationPreview.querySelectorAll('[data-translated-value]')];
      const mergeChoice = translationPreview.querySelector('.merge-choice');
      const syncMergeWarning = () => {
        const values = translatedInputs.map((input) => input.value.trim().toLocaleLowerCase()).filter(Boolean);
        const hasDuplicates = new Set(values).size !== values.length;
        mergeChoice?.classList.toggle('is-needed', hasDuplicates);
        if (mergeChoice) {
          mergeChoice.setAttribute('aria-label', hasDuplicates
            ? 'Attention : certains nouveaux noms sont identiques. Confirmez leur regroupement pour continuer.'
            : 'Autoriser le regroupement uniquement si plusieurs nouveaux noms sont identiques.');
        }
      };
      translatedInputs.forEach((input) => input.addEventListener('input', syncMergeWarning));
      syncMergeWarning();
    }
    if (focus && translationPreview) {
      requestAnimationFrame(() => translationPreview.focus({ preventScroll: true }));
    }
  };

  initializeTranslation(document, { focus: Boolean(document.getElementById('translation-preview')) });

  const processingPage = document.querySelector('[data-processing-status]');
  if (processingPage) {
    const message = processingPage.querySelector('[data-processing-message]');
    const progress = processingPage.querySelector('[data-processing-progress]');
    let pollingStopped = false;
    let pollingFailures = 0;
    const pollProcessingStatus = async () => {
      if (pollingStopped) return;
      try {
        const response = await fetch(processingPage.dataset.statusUrl, {
          credentials: 'same-origin',
          headers: { Accept: 'application/json' },
          cache: 'no-store',
        });
        if (!response.ok) throw new Error('status unavailable');
        const payload = await response.json();
        pollingFailures = 0;
        if (message) message.textContent = payload.message || t('Analyse en cours…');
        if (progress) {
          progress.value = Math.max(0, Math.min(100, Number(payload.progress) || 0));
          progress.textContent = `${progress.value} %`;
        }
        processingPage.classList.toggle('has-failed', payload.status === 'failed');
        if (payload.status === 'ready' && payload.redirect_url) {
          pollingStopped = true;
          window.location.replace(payload.redirect_url);
          return;
        }
        if (payload.status === 'failed') {
          pollingStopped = true;
          return;
        }
      } catch (_error) {
        pollingFailures += 1;
        if (message && pollingFailures >= 3) {
          message.textContent = t('Le suivi est momentanément indisponible. Vous pouvez lancer l’analyse locale ci-dessous.');
        }
      }
      window.setTimeout(pollProcessingStatus, 1200);
    };
    window.setTimeout(pollProcessingStatus, 500);
  }

  const targetSelectors = (value) => String(value || '')
    .split(',')
    .map((selector) => selector.trim())
    .filter(Boolean);

  const showPageMessage = (message, level = 'warn') => {
    const stack = document.querySelector('.flash-stack');
    if (!stack || !message) return;
    stack.replaceChildren();
    const notice = document.createElement('div');
    notice.className = `flash ${level}`;
    notice.setAttribute('role', level === 'warn' ? 'alert' : 'status');
    const icon = document.createElement('span');
    icon.setAttribute('aria-hidden', 'true');
    icon.textContent = level === 'warn' ? '!' : '✓';
    notice.append(icon, document.createTextNode(String(message)));
    stack.append(notice);
  };

  const syncFlashStack = (sourceDocument) => {
    const current = document.querySelector('.flash-stack');
    const incoming = sourceDocument?.querySelector?.('.flash-stack');
    if (!current || !incoming) return;
    current.replaceChildren(...[...incoming.children].map((child) => document.importNode(child, true)));
  };

  const preserveDetailsState = (current, incoming) => {
    if (current.matches?.('details') && current.open) incoming.open = true;
    const currentDetails = [...(current.querySelectorAll?.('details') || [])];
    const incomingDetails = [...(incoming.querySelectorAll?.('details') || [])];
    currentDetails.forEach((details, index) => {
      if (details.open && incomingDetails[index]) incomingDetails[index].open = true;
    });
  };

  const applyDocumentTargets = (sourceDocument, selectors, { allowRemoval = false } = {}) => {
    const uniqueSelectors = [...new Set(selectors)];
    const firstCurrent = uniqueSelectors.map((selector) => document.querySelector(selector)).find(Boolean);
    const firstTop = firstCurrent?.getBoundingClientRect().top;
    let changed = 0;
    let chartsChanged = false;

    uniqueSelectors.forEach((selector) => {
      const current = document.querySelector(selector);
      const incomingSource = sourceDocument.querySelector(selector);
      if (!current) return;
      if (!incomingSource) {
        if (allowRemoval) {
          window.DataPilotCharts?.destroy(current);
          current.remove();
          changed += 1;
        }
        return;
      }

      const incoming = document.importNode(incomingSource, true);
      preserveDetailsState(current, incoming);
      if (current.querySelector?.('canvas') || current.matches?.('canvas')) {
        window.DataPilotCharts?.destroy(current);
        chartsChanged = true;
      }
      if (incoming.querySelector?.('[data-dashboard-chart-data], [data-custom-chart-data]')) {
        chartsChanged = true;
      }
      current.replaceWith(incoming);
      initializeTranslation(incoming);
      changed += 1;
    });

    if (chartsChanged) window.DataPilotCharts?.initialize(document);
    if (typeof firstTop === 'number') {
      const nextFirst = uniqueSelectors.map((selector) => document.querySelector(selector)).find(Boolean);
      if (nextFirst) {
        const delta = nextFirst.getBoundingClientRect().top - firstTop;
        if (Math.abs(delta) > 1) window.scrollBy({ top: delta, behavior: 'auto' });
      }
    }
    return changed;
  };

  const applyHtmlFragment = (fragment) => {
    if (!fragment?.selector || typeof fragment.html !== 'string') return 0;
    const parsed = new DOMParser().parseFromString(fragment.html, 'text/html');
    return applyDocumentTargets(parsed, [fragment.selector]);
  };

  const focusProjectTarget = (selector) => {
    if (!selector) return;
    const target = document.querySelector(selector);
    if (!target) return;
    if (!target.hasAttribute('tabindex') && !/^(INPUT|SELECT|TEXTAREA|BUTTON|A)$/.test(target.tagName)) {
      target.setAttribute('tabindex', '-1');
    }
    requestAnimationFrame(() => {
      target.focus?.({ preventScroll: true });
      const bounds = target.getBoundingClientRect();
      if (bounds.top < 88 || bounds.bottom > window.innerHeight - 24) {
        target.scrollIntoView({ behavior: reducedMotion ? 'auto' : 'smooth', block: 'center' });
      }
    });
  };

  const canonicalProjectUrl = () => document.querySelector('[data-project-nav]')?.dataset.projectUrl || '';

  const setCanonicalProjectLocation = (canonical, focusSelector = '') => {
    const projectUrl = canonical || canonicalProjectUrl();
    if (!projectUrl) return;
    let hash = '';
    if (focusSelector?.startsWith('#')) {
      hash = focusSelector === '#translation-preview' ? '#translation-preview'
        : focusSelector.startsWith('#business-words') || focusSelector === '#translation-notice'
          ? '#business-words'
          : focusSelector;
    }
    window.history.replaceState({}, '', `${projectUrl}${hash}`);
  };

  const fetchProjectDocument = async (url, options = {}) => {
    const response = await fetch(url, {
      credentials: 'same-origin',
      ...options,
      headers: {
        Accept: 'text/html',
        ...(options.headers || {}),
      },
    });
    const contentType = response.headers.get('content-type') || '';
    if (!contentType.includes('text/html')) {
      throw new Error(t('DataPilot a reçu une réponse inattendue. Réessayez dans un instant.'));
    }
    const html = await response.text();
    return {
      response,
      document: new DOMParser().parseFromString(html, 'text/html'),
    };
  };

  const readJsonResponse = async (response) => {
    const contentType = response.headers.get('content-type') || '';
    if (!contentType.includes('application/json')) {
      throw new Error(response.status === 413
        ? t('Le fichier dépasse la taille acceptée.')
        : t('La réponse reçue est incomplète. Rechargez la page puis réessayez.'));
    }
    try {
      return await response.json();
    } catch (_error) {
      throw new Error(t('La réponse reçue est illisible. Rechargez la page puis réessayez.'));
    }
  };

  const refreshProjectTargets = async (url, selectors) => {
    const visibleSelectors = selectors.filter((selector) => document.querySelector(selector));
    if (!url || !visibleSelectors.length) return;
    const result = await fetchProjectDocument(url);
    if (!result.response.ok) throw new Error(t('L’affichage n’a pas pu être actualisé.'));
    const changed = applyDocumentTargets(result.document, visibleSelectors);
    if (!changed) throw new Error(t('Les nouvelles informations n’ont pas pu être affichées.'));
  };

  const submitAsyncProjectForm = async (form) => {
    if (form.getAttribute('aria-busy') === 'true') return;
    const selectors = targetSelectors(form.dataset.refreshTargets);
    const focusSelector = form.dataset.focusTarget || '';
    const expectsJson = form.hasAttribute('data-async-json');
    setLoadingForm(form, true);

    try {
      const action = form.action.split('#')[0];
      if (expectsJson) {
        const response = await fetch(action, {
          method: form.method || 'POST',
          body: new FormData(form),
          credentials: 'same-origin',
          headers: {
            Accept: 'application/json',
            'X-Requested-With': 'DataPilot',
          },
        });
        const payload = await readJsonResponse(response);
        const fragmentChanged = applyHtmlFragment(payload.fragment);
        if (!fragmentChanged && !response.ok) {
          throw new Error(payload.error?.message || payload.message || t('La modification n’a pas pu être appliquée.'));
        }
        if (payload.refresh_url && Array.isArray(payload.refresh_targets) && payload.refresh_targets.length) {
          await refreshProjectTargets(payload.refresh_url, payload.refresh_targets);
        }
        setCanonicalProjectLocation(payload.canonical_url, payload.fragment?.focus || focusSelector);
        focusProjectTarget(payload.fragment?.focus || focusSelector);
        if (!response.ok && !payload.fragment) {
          showPageMessage(payload.error?.message || payload.message, 'warn');
        }
        return;
      }

      const result = await fetchProjectDocument(action, {
        method: form.method || 'POST',
        body: new FormData(form),
      });
      const isProjectDocument = Boolean(result.document.querySelector('[data-project-nav]'));
      const changed = applyDocumentTargets(result.document, selectors, { allowRemoval: isProjectDocument });
      syncFlashStack(result.document);
      if (!changed) {
        throw new Error(result.response.ok
          ? t('La modification a été enregistrée, mais l’affichage n’a pas pu être actualisé.')
          : t('La modification n’a pas pu être appliquée.'));
      }
      const canonical = result.document.querySelector('[data-project-nav]')?.dataset.projectUrl;
      setCanonicalProjectLocation(canonical, focusSelector);
      focusProjectTarget(focusSelector);
    } catch (error) {
      showPageMessage(error?.message || t('La connexion a été interrompue. Vos données sont restées intactes.'), 'warn');
    } finally {
      if (document.contains(form)) setLoadingForm(form, false);
    }
  };

  document.addEventListener('submit', (event) => {
    const form = event.target.closest?.('form[data-loading-form]');
    if (!form || form.matches('[data-async-chat]') || event.defaultPrevented) return;
    const confirmation = form.dataset.confirm;
    if (confirmation && !window.confirm(confirmation)) {
      event.preventDefault();
      return;
    }
    if (form.matches('[data-async-project]')) {
      event.preventDefault();
      submitAsyncProjectForm(form);
      return;
    }
    setLoadingForm(form, true);
  });

  const initialProjectUrl = canonicalProjectUrl();
  if (initialProjectUrl && location.pathname !== new URL(initialProjectUrl, location.origin).pathname) {
    window.history.replaceState({}, '', `${initialProjectUrl}${location.hash || ''}`);
  }

  const createTextElement = (tag, className, text) => {
    const element = document.createElement(tag);
    if (className) element.className = className;
    element.textContent = text;
    return element;
  };

  const appendAnswerList = (answer, title, items, className = '') => {
    if (!Array.isArray(items) || !items.length) return;
    const section = document.createElement('section');
    section.className = `answer-points ${className}`.trim();
    section.append(createTextElement('h3', '', title));
    const list = document.createElement('ul');
    items.forEach((item) => list.append(createTextElement('li', '', String(item))));
    section.append(list);
    answer.append(section);
  };

  const appendSuggestedChart = (answer, payload) => {
    const chart = payload.chart;
    const spec = payload.chart_spec;
    if (!chart || !spec || !Array.isArray(chart.labels) || !Array.isArray(chart.values)) return;

    const section = document.createElement('section');
    section.className = 'answer-chart';
    section.append(createTextElement('h3', '', t('Graphique suggéré')));
    if (payload.chart_message) section.append(createTextElement('p', 'answer-chart-intro', payload.chart_message));
    const canvasWrap = document.createElement('div');
    canvasWrap.className = 'answer-chart-canvas';
    const canvas = document.createElement('canvas');
    canvas.height = 210;
    canvas.setAttribute('role', 'img');
    canvas.setAttribute('aria-label', `${t('Graphique suggéré')} : ${chart.title || ''}`);
    canvasWrap.append(canvas);
    section.append(canvasWrap);

    const values = document.createElement('details');
    values.className = 'answer-chart-values';
    values.append(createTextElement('summary', '', t('Voir les valeurs du graphique')));
    const tableWrap = document.createElement('div');
    tableWrap.className = 'mini-table-wrap';
    const table = document.createElement('table');
    const head = document.createElement('thead');
    const headRow = document.createElement('tr');
    const labelHead = createTextElement('th', '', t('Élément'));
    const valueHead = createTextElement('th', '', t('Valeur'));
    labelHead.setAttribute('scope', 'col');
    valueHead.setAttribute('scope', 'col');
    headRow.append(labelHead, valueHead);
    head.append(headRow);
    const body = document.createElement('tbody');
    chart.values.forEach((value, index) => {
      const row = document.createElement('tr');
      row.append(createTextElement('td', '', String(chart.labels[index] ?? '')), createTextElement('td', '', String(value)));
      body.append(row);
    });
    table.append(head, body);
    tableWrap.append(table);
    values.append(tableWrap);
    section.append(values);

    if (payload.pin_url) {
      const pin = createTextElement('button', 'btn secondary answer-pin-chart', t('Ajouter à mes graphiques'));
      pin.type = 'button';
      pin.dataset.pinChart = 'true';
      pin.dataset.pinUrl = payload.pin_url;
      pin.dataset.dimension = spec.dimension || '';
      pin.dataset.measure = spec.measure || '';
      pin.dataset.aggregation = spec.aggregation || 'count';
      pin.dataset.chartType = spec.chart_type || chart.type || 'auto';
      section.append(pin);
      const status = createTextElement('span', 'answer-pin-status', '');
      status.setAttribute('role', 'status');
      status.setAttribute('aria-live', 'polite');
      section.append(status);
    }
    answer.append(section);

    window.setTimeout(() => {
      if (typeof window.renderDataPilotChart === 'function') window.renderDataPilotChart(canvas, chart);
      else canvasWrap.append(createTextElement('p', 'chart-render-error', t('Le graphique ne peut pas être affiché pour le moment.')));
    }, 0);
  };

  const buildAnswer = (payload) => {
    const answer = document.createElement('article');
    answer.className = `answer${payload.ok ? '' : ' uncertain'}`;
    const label = payload.ok
      ? (payload.source === 'groq' ? t('Votre explication approfondie') : t('Votre réponse'))
      : t('Réponse indisponible');
    answer.append(createTextElement('small', 'answer-label', label));
    const summary = createTextElement('p', 'answer-summary', payload.answer || t('La réponse est indisponible.'));
    summary.setAttribute('dir', 'auto');
    answer.append(summary);
    appendAnswerList(answer, t('À retenir'), payload.insights);
    appendAnswerList(answer, t('À vérifier'), payload.cautions, 'cautions');
    appendSuggestedChart(answer, payload);

    if (Array.isArray(payload.evidence) && payload.evidence.length) {
      const evidence = document.createElement('div');
      evidence.className = 'evidence-list';
      payload.evidence.forEach((item) => evidence.append(createTextElement('div', 'answer-evidence', `${t('Méthode')} · ${item}`)));
      answer.append(evidence);
    }

    if (Array.isArray(payload.suggested_questions) && payload.suggested_questions.length) {
      const followups = document.createElement('div');
      followups.className = 'followups';
      followups.append(createTextElement('strong', '', t('Vous pouvez aussi demander :')));
      payload.suggested_questions.forEach((item) => {
        const button = createTextElement('button', 'followup-question', String(item));
        button.type = 'button';
        button.dataset.question = String(item);
        followups.append(button);
      });
      answer.append(followups);
    }
    return answer;
  };

  const buildPendingTurn = (question) => {
    const turn = document.createElement('div');
    turn.className = 'chat-turn';
    const displayedQuestion = createTextElement('p', 'chat-question', question);
    displayedQuestion.setAttribute('dir', 'auto');
    turn.append(displayedQuestion);
    const pending = document.createElement('article');
    pending.className = 'answer pending';
    pending.append(createTextElement('small', 'answer-label', t('Analyse en cours')));
    const thinking = document.createElement('div');
    thinking.className = 'chat-thinking';
    thinking.setAttribute('aria-label', t('DataPilot prépare votre réponse'));
    for (let index = 0; index < 3; index += 1) thinking.append(document.createElement('i'));
    pending.append(thinking);
    turn.append(pending);
    return { turn, pending };
  };

  const chatbot = document.querySelector('[data-chatbot]');
  const chatPanel = chatbot?.querySelector('[data-chatbot-panel]');
  const chatToggle = chatbot?.querySelector('[data-chatbot-toggle]');
  const chatClose = chatbot?.querySelector('[data-chatbot-close]');
  const chatInput = chatbot?.querySelector('[data-chatbot-input]');
  const chatProject = chatbot?.dataset.chatProject || '';
  const chatHistoryKey = chatProject ? `datapilot-chat-history:${chatProject}` : '';
  const chatOpenKey = chatProject ? `datapilot-chat-open:${chatProject}` : '';
  let chatHistory = [];

  const setChatbotOpen = (open, { focus = true } = {}) => {
    if (!chatbot || !chatPanel || !chatToggle) return;
    chatbot.dataset.chatOpen = open ? 'true' : 'false';
    chatPanel.hidden = !open;
    chatToggle.setAttribute('aria-expanded', open ? 'true' : 'false');
    try {
      if (chatOpenKey) sessionStorage.setItem(chatOpenKey, open ? 'true' : 'false');
    } catch (_error) {
      // The assistant stays usable when browser storage is unavailable.
    }
    if (open && focus) window.setTimeout(() => chatInput?.focus(), 80);
    if (!open && focus) chatToggle.focus();
  };

  const persistChatHistory = () => {
    if (!chatHistoryKey) return;
    try {
      sessionStorage.setItem(chatHistoryKey, JSON.stringify(chatHistory.slice(-6)));
    } catch (_error) {
      // A full or disabled session storage must not block questions.
    }
  };

  const appendStoredTurn = (region, item) => {
    if (!region || !item?.question || !item?.payload) return;
    const turn = document.createElement('div');
    turn.className = 'chat-turn';
    const question = createTextElement('p', 'chat-question', String(item.question));
    question.setAttribute('dir', 'auto');
    turn.append(question, buildAnswer(item.payload));
    region.append(turn);
  };

  const chatForm = document.querySelector('[data-async-chat]');
  let activeChatRequest = null;
  let chatRequestId = 0;

  if (chatbot && chatForm) {
    const region = chatbot.querySelector('[data-answer-region]');
    try {
      const storedHistory = JSON.parse(sessionStorage.getItem(chatHistoryKey) || '[]');
      chatHistory = Array.isArray(storedHistory)
        ? storedHistory.filter((item) => item?.question && item?.payload).slice(-6)
        : [];
    } catch (_error) {
      chatHistory = [];
    }
    if (region && chatHistory.length) {
      region.querySelectorAll('.chat-turn:not([data-chatbot-welcome])').forEach((turn) => turn.remove());
      chatHistory.forEach((item) => appendStoredTurn(region, item));
      region.scrollTop = region.scrollHeight;
    }
    let initiallyOpen = chatbot.dataset.chatOpen === 'true';
    try {
      initiallyOpen = initiallyOpen || sessionStorage.getItem(chatOpenKey) === 'true';
    } catch (_error) {
      // Use the server-provided initial state.
    }
    setChatbotOpen(initiallyOpen, { focus: false });
    chatToggle?.addEventListener('click', () => setChatbotOpen(true));
    chatClose?.addEventListener('click', () => setChatbotOpen(false));
    document.querySelectorAll('[data-open-chatbot]').forEach((button) => {
      button.addEventListener('click', () => setChatbotOpen(true));
    });
    chatbot.querySelectorAll('[data-chat-suggestion]').forEach((button) => {
      button.addEventListener('click', () => {
        if (!chatInput || chatForm.getAttribute('aria-busy') === 'true') return;
        chatInput.value = button.dataset.chatSuggestion || '';
        setChatbotOpen(true, { focus: false });
        chatForm.requestSubmit();
      });
    });
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && chatbot.dataset.chatOpen === 'true') setChatbotOpen(false);
    });
  }

  chatForm?.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (!chatForm.reportValidity() || chatForm.getAttribute('aria-busy') === 'true') return;

    const input = chatForm.querySelector('[name="question"]');
    const region = chatbot?.querySelector('[data-answer-region]') || chatForm.querySelector('[data-answer-region]');
    const question = input?.value.trim() || '';
    if (!question || !region) return;

    const requestId = ++chatRequestId;
    activeChatRequest?.abort();
    const controller = new AbortController();
    activeChatRequest = controller;
    const timeout = window.setTimeout(() => controller.abort(), 45000);
    const { turn, pending } = buildPendingTurn(question);
    region.append(turn);
    while (region.children.length > 6) region.firstElementChild?.remove();
    region.scrollTo({ top: region.scrollHeight, behavior: reducedMotion ? 'auto' : 'smooth' });
    setLoadingForm(chatForm, true);

    try {
      const action = chatForm.action.split('#')[0];
      const response = await fetch(action, {
        method: 'POST',
        body: new FormData(chatForm),
        credentials: 'same-origin',
        headers: {
          Accept: 'application/json',
          'X-Requested-With': 'DataPilot',
        },
        signal: controller.signal,
      });
      const payload = await readJsonResponse(response);
      if (requestId !== chatRequestId) return;
      pending.replaceWith(buildAnswer(payload));
      chatHistory.push({ question, payload });
      chatHistory = chatHistory.slice(-6);
      persistChatHistory();
      if (response.ok && input.value.trim() === question) input.value = '';
    } catch (error) {
      if (requestId !== chatRequestId) return;
      const timedOut = error?.name === 'AbortError';
      pending.replaceWith(buildAnswer({
        ok: false,
        answer: timedOut
          ? t('La réponse prend trop de temps. Réessayez dans un instant.')
          : t('La connexion avec DataPilot a été interrompue. Votre page et votre fichier sont restés intacts.'),
        evidence: [],
        suggested_questions: [],
      }));
    } finally {
      window.clearTimeout(timeout);
      if (requestId === chatRequestId) {
        activeChatRequest = null;
        setLoadingForm(chatForm, false);
        region.scrollTo({ top: region.scrollHeight, behavior: reducedMotion ? 'auto' : 'smooth' });
      }
    }
  });

  document.addEventListener('click', async (event) => {
    const briefButton = event.target.closest('[data-brief-speak]');
    if (briefButton) {
      if ('speechSynthesis' in window) {
        speechSynthesis.cancel();
        const section = briefButton.closest('.business-brief');
        const words = [section?.querySelector('.business-brief-total')?.innerText,
          section?.querySelector('.business-brief-finding')?.innerText,
          section?.querySelector('.business-brief li')?.innerText].filter(Boolean).join('. ');
        const message = new SpeechSynthesisUtterance(words);
        message.lang = window.DP_LANG === 'en' ? 'en-US' : 'fr-FR';
        message.rate = 0.9;
        speechSynthesis.speak(message);
      }
      return;
    }
    const pinButton = event.target.closest('[data-pin-chart]');
    if (pinButton) {
      const status = pinButton.parentElement?.querySelector('.answer-pin-status');
      const csrf = chatForm?.querySelector('[name="_csrf"]')?.value || '';
      const data = new FormData();
      data.append('_csrf', csrf);
      data.append('dimension', pinButton.dataset.dimension || '');
      data.append('measure', pinButton.dataset.measure || '');
      data.append('aggregation', pinButton.dataset.aggregation || 'count');
      data.append('chart_type', pinButton.dataset.chartType || 'auto');
      pinButton.disabled = true;
      const originalText = pinButton.textContent;
      pinButton.textContent = t('Ajout en cours…');
      try {
        const response = await fetch(pinButton.dataset.pinUrl, {
          method: 'POST', body: data, credentials: 'same-origin',
          headers: { Accept: 'application/json', 'X-Requested-With': 'DataPilot' },
        });
        const payload = await readJsonResponse(response);
        if (!response.ok || !payload.ok) throw new Error(payload.message || 'Ajout impossible');
        pinButton.textContent = t('Ajouté à mes graphiques');
        if (status) status.textContent = payload.message;
      } catch (error) {
        pinButton.disabled = false;
        pinButton.textContent = originalText;
        if (status) status.textContent = error?.message || t('Le graphique n’a pas pu être ajouté.');
      }
      return;
    }

    const followupButton = event.target.closest('.followup-question');
    if (!followupButton) return;
    const input = chatForm?.querySelector('[name="question"]');
    if (!input) return;
    setChatbotOpen(true, { focus: false });
    input.value = followupButton.dataset.question || followupButton.textContent.trim();
    input.focus();
  });

  window.addEventListener('beforeunload', () => activeChatRequest?.abort());

  const nav = document.querySelector('[data-project-nav]');
  const spyLinks = [...document.querySelectorAll('[data-spy-link]')];
  if (nav && spyLinks.length && 'IntersectionObserver' in window) {
    const sections = spyLinks.map((link) => document.querySelector(link.getAttribute('href'))).filter(Boolean);
    const sectionVisibility = new Map(sections.map((section) => [section, 0]));
    const activateLink = (activeSection) => {
      const activeLink = spyLinks.find((link) => link.getAttribute('href') === `#${activeSection.id}`);
      spyLinks.forEach((link) => {
        const active = link === activeLink;
        link.classList.toggle('is-active', active);
        if (active) link.setAttribute('aria-current', 'location');
        else link.removeAttribute('aria-current');
      });
      if (activeLink && window.innerWidth <= 620) {
        nav.scrollTo({
          left: activeLink.offsetLeft - (nav.clientWidth - activeLink.offsetWidth) / 2,
          behavior: reducedMotion ? 'auto' : 'smooth',
        });
      }
    };

    const initialSection = sections.find((section) => `#${section.id}` === location.hash);
    if (initialSection) activateLink(initialSection);

    const spyObserver = new IntersectionObserver((entries) => {
      entries.forEach((entry) => sectionVisibility.set(entry.target, entry.isIntersecting ? entry.intersectionRatio : 0));
      const visible = sections
        .map((section) => ({ section, ratio: sectionVisibility.get(section) || 0 }))
        .filter((item) => item.ratio > 0)
        .sort((a, b) => b.ratio - a.ratio)[0];
      if (visible) activateLink(visible.section);
    }, { rootMargin: '-42% 0px -52% 0px', threshold: [0.01, 0.25] });
    sections.forEach((section) => spyObserver.observe(section));
  }
})();
