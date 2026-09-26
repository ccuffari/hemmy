"""Core dell'agente: loop ReAct (planner + executor + state).

Ciclo di ragionamento:

    Thought        -> il modello ragiona sul prossimo passo
    Action         -> nome del tool da eseguire
    Action Input   -> argomenti del tool in JSON
    Observation    -> risultato del tool (aggiunto dal sistema)
    ... (ripetuto fino a Final Answer o max_iterations)

Componenti:
- Planner: interroga il provider LLM configurato dall'utente (BYOK multi-provider:
  DeepSeek/OpenAI/Gemini/Llama/self-hosted via SDK OpenAI-compatibile, oppure
  Anthropic Claude via il proprio SDK — vedi `core/llm_providers.py`).
- Executor: valida col guardrail e instrada le Action verso i tool registrati.
- State: cronologia dei passi (memoria di sessione).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from hemmy.core.llm_providers import LLMClient, resolve_llm_config
from hemmy.utils.helpers import redact_secrets

# Limite di caratteri per una singola Observation passata al modello.
_MAX_OBSERVATION_CHARS = 4000

# Tool di LETTURA che restituiscono contenuto file/log: qui il cap standard (4000)
# taglierebbe il contenuto rendendo impossibile leggere/correggere un file intero
# (l'agente finirebbe per riscriverlo "a memoria", perdendo pezzi). Per questi tool
# si usa un cap molto più ampio ma comunque limitato (anti-saturazione del contesto).
_LARGE_READ_ACTIONS = {
    "github.get_file",
    "github.get_files",
    "github.read_folder",
    "iac.show",
    "iac.state_list",
    "iac.output",
    "cicd.get_run_logs",
}
_MAX_READ_OBSERVATION_CHARS = 40000

# Parametri "infrastrutturali" legati ai tool via functools.partial in cli.py per
# instradarli in modo sicuro (dove scrivere, di chi è il dato, quali documenti
# passare). NON sono argomenti che il modello deve mai poter impostare: vengono
# rimossi da ogni Action Input prima della chiamata, indipendentemente dal tool
# (vedi `_execute_action`). Se un domani un tool avesse legittimamente bisogno che
# il modello passi un parametro con questo nome, andrebbe rinominato nel tool
# stesso — mai rimosso da questa lista.
_RESERVED_ARG_NAMES = {
    "base_dir",       # meta.install_tool/list_plugins/remove_plugin: cartella plugin
    "owner_user_id",  # meta.install_tool/remove_plugin: proprietario su Supabase
    "workdir",        # iac.*: working directory Terraform (mai un path arbitrario)
    "uploads_dir",    # files.*: cartella upload (mai un path arbitrario)
    "tool_docs",      # meta.list_capabilities/analyze_request: catalogo interno
    "secret_provider",  # github.set_secret: provider di segreti iniettato
}


@dataclass
class AgentState:
    """Stato della sessione: traccia dei passi ReAct."""

    question: str = ""
    steps: list[dict[str, Any]] = field(default_factory=list)
    iterations: int = 0
    finished: bool = False
    final_answer: str | None = None


class ADFAgent:
    """Agente ReAct per la diagnostica di pipeline ADF."""

    def __init__(
        self,
        config: dict[str, Any],
        prompts: dict[str, str],
        tools: dict[str, Callable[..., Any]],
        guardrails: Any,
        memory: Any,
        tool_docs: dict[str, str] | None = None,
        audit: Any = None,
        docs: Any = None,
        anonymizer: Any = None,
        llm_config: dict[str, Any] | None = None,
    ) -> None:
        """`llm_config` (BYOK): {"provider", "model", "base_url", "api_key"}.

        Se assente, ripiega sulle variabili d'ambiente `DEEPSEEK_*` — SOLO per l'uso
        CLI locale senza portale (nessun utente autenticato): nel percorso web
        multi-utente `interfaces/web.py` lo valorizza SEMPRE dalle Impostazioni
        dell'utente (provider/modello non segreti + API key cifrata personale).
        """
        self.config = config
        self.prompts = prompts
        self.tools = tools
        self.guardrails = guardrails
        self.memory = memory
        self.tool_docs = tool_docs or {}
        self.audit = audit
        self.docs = docs
        self.anonymizer = anonymizer
        self._docs_dirty = False
        self.llm_config = llm_config or resolve_llm_config(
            settings=None, api_key=os.getenv("DEEPSEEK_API_KEY"), agent_yaml=config
        )
        self.client = self._build_llm_client()
        # Conversazione persistente per l'intera sessione: mantiene il contesto tra un
        # turno e l'altro (domande, Thought/Action, Observation, risposte).
        self._conversation: list[dict[str, str]] = [
            {"role": "system", "content": self._system_message()}
        ]
        # Messaggi conservati oltre al system prompt (finestra scorrevole).
        self._max_history = int(config.get("max_history", 40))
        # Guardia anti-loop: quante volte la STESSA (action, args) può ripetersi in un
        # turno prima di essere bloccata (evita commit->trigger->fail all'infinito).
        self._max_repeat = int(config.get("max_action_repeat", 2))

    # ------------------------------------------------------------------ LLM

    def _build_llm_client(self) -> LLMClient:
        """Crea il client BYOK per il provider LLM scelto dall'utente (o dall'env
        var `DEEPSEEK_*` in fallback, solo per l'uso CLI locale senza portale)."""
        return LLMClient(
            provider=self.llm_config["provider"],
            api_key=self.llm_config.get("api_key"),
            model=self.llm_config.get("model"),
            base_url=self.llm_config.get("base_url"),
            timeout=self.config.get("timeout", 120),
            max_retries=self.config.get("max_retries", 3),
        )

    def _call_llm(self, messages: list[dict[str, str]]) -> str:
        """Invoca il modello (qualunque provider BYOK) e restituisce il testo.

        Il retry sugli errori transitori è già gestito da `LLMClient.complete`.
        """
        return self.client.complete(messages, temperature=self.config.get("temperature", 0.1))

    # -------------------------------------------------------------- Prompts

    def _system_message(self) -> str:
        """Compone il messaggio di sistema: ruolo + istruzioni ReAct + tool disponibili."""
        tool_list = "\n".join(
            f"- {name}: {self.tool_docs.get(name, 'tool')}" for name in self.tools
        )
        return (
            f"{self.prompts.get('system', '')}\n\n"
            f"{self.prompts.get('planner', '')}\n\n"
            f"{self.prompts.get('guardrail', '')}\n\n"
            "## Tool disponibili\n"
            f"{tool_list}\n\n"
            "## Formato di risposta OBBLIGATORIO\n"
            "A ogni passo produci ESATTAMENTE uno tra:\n"
            "1) un passo di azione:\n"
            "Thought: <ragionamento>\n"
            "Action: <nome_tool>\n"
            "Action Input: <oggetto JSON con gli argomenti, {} se nessuno>\n"
            "2) la risposta finale:\n"
            "Final Answer: <diagnosi, evidenze e azioni consigliate>\n"
        )

    # -------------------------------------------------------------- Parsing

    @staticmethod
    def _extract_json_object(text: str, after_key: str) -> str | None:
        """Estrae il primo oggetto JSON bilanciato dopo `after_key`.

        Scandisce le graffe tenendo conto delle stringhe (e degli escape), così i JSON
        annidati (definizioni ADF di dataset/pipeline) vengono catturati per intero e
        non troncati alla prima graffa interna.
        """
        idx = text.find(after_key)
        if idx == -1:
            return None
        start = text.find("{", idx)
        if start == -1:
            return None

        depth = 0
        in_string = False
        escaped = False
        for pos in range(start, len(text)):
            ch = text[pos]
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
            else:
                if ch == '"':
                    in_string = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        return text[start : pos + 1]
        return None

    @staticmethod
    def _parse_action(text: str) -> tuple[str | None, dict[str, Any]]:
        """Estrae (action, args) da un output del modello. (None, {}) se assente."""
        action_match = re.search(r"Action:\s*([^\n`]+)", text)
        if not action_match:
            return None, {}
        action = action_match.group(1).strip().strip("`").strip()

        raw = ADFAgent._extract_json_object(text, "Action Input:")
        args: dict[str, Any] = {}
        if raw:
            try:
                args = json.loads(raw)
            except json.JSONDecodeError:
                args = {}
        return action, args

    # ------------------------------------------------------------- Executor

    @staticmethod
    def _pre_execution_gate(action: str, args: dict[str, Any]) -> str | None:
        """Gate deterministici PRIMA dell'esecuzione. Ritorna un messaggio bloccante
        (da usare come Observation, senza eseguire il tool) oppure None se via libera.

        Gate attivo: lint pre-commit OBBLIGATORIO. Prima di `github.commit_files`, i file
        Terraform (.tf/.tfvars) passano da iac.lint_files; se ci sono errori bloccanti
        (placeholder residui, graffe sbilanciate) il commit è RIFIUTATO — evita di
        pubblicare codice che farebbe fallire la pipeline. È un gate reale, non un
        consiglio: non dipende dal fatto che il modello ricordi di chiamare il lint.
        """
        if action != "github.commit_files":
            return None
        files = (args or {}).get("files") or {}
        if not isinstance(files, dict):
            return None
        tf = {
            k: v
            for k, v in files.items()
            if isinstance(k, str) and k.endswith((".tf", ".tfvars"))
        }
        if not tf:
            return None
        try:
            from hemmy.tools.iac import iac_tools

            res = iac_tools.lint_files(tf)
        except Exception:  # noqa: BLE001 - il gate non deve rompere il turno
            return None
        if res.get("ok", True):
            return None
        errs = "; ".join(res.get("errors", [])) or "(errori non specificati)"
        warns = "; ".join(res.get("warnings", [])) or "nessuno"
        return (
            "COMMIT BLOCCATO dal lint pre-commit: i file Terraform contengono errori "
            f"bloccanti che farebbero fallire il deploy: {errs}. Correggi il contenuto e "
            f"ricommitta. (Warning non bloccanti: {warns}.)"
        )

    def _execute_action(self, action: str, args: dict[str, Any]) -> Any:
        """Valida col guardrail, registra l'audit ed esegue il tool registrato."""
        if action not in self.tools:
            raise KeyError(
                f"Tool sconosciuto: '{action}'. Disponibili: {', '.join(self.tools)}"
            )

        is_write = self.guardrails.is_write_action(action)

        # Validazione + approvazione umana.
        try:
            self.guardrails.check(action, args)
        except Exception as exc:  # noqa: BLE001 - audit poi rilancio
            if is_write and self.audit:
                self.audit.record(action, args, "denied", exc)
            raise

        # Sicurezza: alcuni tool sono legati (via functools.partial in cli.py) a
        # parametri "interni" che decidono DOVE scrivere/chi possiede il dato
        # (es. la cartella isolata dell'utente per i plugin generati). Poiché
        # l'esecuzione avviene come `self.tools[action](**args)`, un kwarg dello
        # STESSO nome nel JSON prodotto dal modello sovrascriverebbe silenziosamente
        # quello "sicuro" già legato dal partial — permettendo di far scrivere un
        # tool fuori dalla propria area (es. dentro il codice sorgente
        # dell'applicazione) o di falsificare l'owner di un dato. Questi nomi non
        # sono MAI argomenti legittimi lato modello: vengono rimossi prima di ogni
        # chiamata, qualunque sia il tool.
        for _reserved in _RESERVED_ARG_NAMES:
            args.pop(_reserved, None)

        # Esecuzione.
        try:
            result = self.tools[action](**args)
        except Exception as exc:  # noqa: BLE001 - audit poi rilancio
            if is_write and self.audit:
                self.audit.record(action, args, "error", exc)
            raise

        if is_write and self.audit:
            self.audit.record(action, args, "success")

        # Documentazione vivente: aggiorna lo stato infrastruttura sulle scritture.
        if is_write and self.docs and self.docs.apply(action, args, result):
            self._docs_dirty = True
        return result

    # ----------------------------------------------------------------- Loop

    def _plan(self, messages: list[dict[str, str]]) -> str:
        """Fase Planner: chiede al LLM il prossimo Thought/Action o la Final Answer."""
        return self._call_llm(messages)

    @staticmethod
    def _progress_summary(state: "AgentState") -> str:
        """Riepilogo di progresso quando si esaurisce il budget di iterazioni.

        Invece del messaggio secco "Limite raggiunto", elenca gli ultimi passi
        (azione -> osservazione troncata) così l'utente sa cosa è stato fatto e cosa
        manca, e può dire 'continua' (il contesto è preservato).
        """
        lines: list[str] = []
        for i, step in enumerate(state.steps[-8:], 1):
            act = step.get("action") or "(formato non valido)"
            obs = (step.get("observation") or "").replace("\n", " ")[:180]
            lines.append(f"{i}. {act} → {obs}")
        body = "\n".join(lines) if lines else "(nessun passo eseguito)"
        return (
            f"⚠️ Raggiunto il limite di {state.iterations} iterazioni senza una risposta "
            "finale. Non è un errore dell'infrastruttura: ho solo esaurito i passi "
            "disponibili in questo turno.\n\n"
            "Riepilogo di quanto ho fatto (ultimi passi):\n"
            f"{body}\n\n"
            "Come proseguire: scrivi 'continua' per riprendere da qui (il contesto della "
            "sessione è preservato), oppure restringi la richiesta a un singolo passo. "
            "Se un'azione si ripeteva senza successo, indicami il dato mancante "
            "(es. nome/valore corretto) così sblocco il flusso."
        )

    def _trim_history(self) -> None:
        """Mantiene il system prompt + gli ultimi `_max_history` messaggi."""
        if len(self._conversation) > self._max_history + 1:
            self._conversation = [self._conversation[0]] + self._conversation[
                -self._max_history :
            ]

    # --------------------------------------------------- Persistenza sessione

    @property
    def conversation(self) -> list[dict[str, str]]:
        return self._conversation

    def load_conversation(self, messages: list[dict[str, str]] | None) -> None:
        """Ripristina una conversazione salvata (mantiene comunque il system corrente)."""
        if not messages:
            return
        system = {"role": "system", "content": self._system_message()}
        restored = [m for m in messages if m.get("role") != "system"]
        self._conversation = [system] + restored
        self._trim_history()

    def reset_conversation(self) -> None:
        """Azzera il contesto della sessione."""
        self._conversation = [{"role": "system", "content": self._system_message()}]
        self.memory.clear()

    def run(self, question: str, on_event: Callable[[dict[str, Any]], None] | None = None) -> str:
        """Esegue il loop ReAct fino a Final Answer o max_iterations.

        La conversazione è persistente tra i turni della sessione: la cronologia
        precedente (domande, azioni, osservazioni, risposte) resta nel contesto.

        on_event: callback opzionale fn(event: dict) per lo streaming (UI web).
        Emette eventi {type: thought|action|observation|final|error, ...}. I segreti
        non transitano mai da qui (le Observation sono già redatte/anonimizzate).
        """

        def emit(**ev: Any) -> None:
            if on_event:
                try:
                    on_event(ev)
                except Exception:  # noqa: BLE001 - lo streaming non deve rompere il loop
                    pass

        state = AgentState(question=question)
        self.memory.add("user", question)

        # Anonimizza la domanda (i nomi reali già noti diventano alias) prima dell'LLM.
        q_for_llm = self.anonymizer.anonymize(question) if self.anonymizer else question
        self._conversation.append({"role": "user", "content": f"Domanda: {q_for_llm}"})
        max_iters = self.config.get("max_iterations", 10)

        # Conteggio delle (action, args) eseguite nel turno per la guardia anti-loop.
        executed: dict[str, int] = {}

        while not state.finished and state.iterations < max_iters:
            state.iterations += 1

            try:
                text = self._plan(self._conversation)
            except Exception as exc:  # noqa: BLE001 - il turno non deve crashare l'app
                self._trim_history()
                return (
                    f"⚠️ Errore di comunicazione con il modello LLM ({type(exc).__name__}: "
                    f"{exc}). Il contesto della sessione è preservato: riprova tra poco "
                    "(es. 'ricontrolla') o riformula la richiesta."
                )
            self._conversation.append({"role": "assistant", "content": text})
            self.memory.add("assistant", text)

            if "Final Answer:" in text:
                state.final_answer = text.split("Final Answer:", 1)[1].strip()
                state.finished = True
                break

            # Emette il "Thought" (ragionamento) verso la UI, se presente.
            if "Thought:" in text:
                thought = text.split("Thought:", 1)[1]
                thought = thought.split("Action:", 1)[0].strip()
                if thought:
                    emit(type="thought", text=self.anonymizer.deanonymize(thought) if self.anonymizer else thought)

            action, args = self._parse_action(text)
            if action is None:
                observation = (
                    "Formato non valido. Usa 'Action:' + 'Action Input:' "
                    "oppure 'Final Answer:'."
                )
            else:
                # De-anonimizza gli args (alias -> nomi reali) PRIMA di eseguire il tool
                # e prima dell'approvazione umana (l'operatore vede i nomi reali).
                if self.anonymizer:
                    args = self.anonymizer.deanonymize_obj(args)
                    self.anonymizer.register_from_result(args)
                # Descrizione "parlante" del tool per la UI: prima frase della doc,
                # senza il tag [WRITE]/prefissi tecnici, + flag scrittura.
                _doc = str(self.tool_docs.get(action, "")).replace("[WRITE]", "").strip()
                _doc_short = _doc.split(". Args")[0].split("Args:")[0].strip().rstrip(".")
                _doc_short = _doc_short.split(". ")[0].strip() if _doc_short else ""
                try:
                    _is_write = self.guardrails.is_write_action(action)
                except Exception:  # noqa: BLE001
                    _is_write = False
                emit(type="action", action=action, args=args, doc=_doc_short, write=_is_write)
                # Guardia anti-loop: applicata SOLO alle azioni di scrittura. Ripetere una
                # scrittura identica (commit_files/trigger_pipeline) senza progresso è il
                # loop dannoso da fermare; ripetere LETTURE/polling (wait_for_run, get_status,
                # get_run_logs, get_file) è legittimo — serve a seguire lo stato reale.
                # Gate deterministico pre-esecuzione (es. lint pre-commit obbligatorio).
                gate_block = self._pre_execution_gate(action, args)
                try:
                    is_write = self.guardrails.is_write_action(action)
                except Exception:  # noqa: BLE001 - in dubbio, non bloccare
                    is_write = False
                sig = f"{action}|{json.dumps(args, sort_keys=True, default=str)}"
                # Un'azione bloccata dal gate non conta come tentativo eseguito: dopo la
                # correzione il ri-commit non deve essere penalizzato dalla guardia anti-loop.
                if is_write and gate_block is None:
                    executed[sig] = executed.get(sig, 0) + 1
                if gate_block is not None:
                    observation = gate_block
                elif is_write and executed[sig] > self._max_repeat:
                    observation = (
                        f"AZIONE RIPETUTA BLOCCATA: la scrittura '{action}' con gli stessi "
                        f"argomenti è già stata eseguita {executed[sig] - 1} volte in questo "
                        "turno senza sbloccare la situazione. NON ripeterla. Cambia approccio: "
                        "usa un tool di LETTURA per verificare lo stato reale (es. "
                        "github.get_file, cicd.get_run_logs, i tool list/get), correggi gli "
                        "argomenti, oppure fornisci una 'Final Answer' che riassume lo stato "
                        "attuale e il dato mancante per procedere."
                    )
                else:
                    try:
                        result = self._execute_action(action, args)
                        if self.anonymizer:
                            self.anonymizer.register_from_result(result)
                        observation = json.dumps(result, default=str, ensure_ascii=False)
                        # Cap più ampio per i tool che restituiscono contenuto file/log,
                        # così l'agente può leggere/correggere un file intero senza troncamento.
                        cap = (
                            _MAX_READ_OBSERVATION_CHARS
                            if action in _LARGE_READ_ACTIONS
                            else _MAX_OBSERVATION_CHARS
                        )
                        observation = observation[:cap]
                    except Exception as exc:  # noqa: BLE001 - riportato al modello
                        observation = f"ERRORE: {exc}"

            # Rete di sicurezza: nessun segreto, e nessun nome reale, verso il modello.
            observation = redact_secrets(observation)
            if self.anonymizer:
                observation = self.anonymizer.anonymize(observation)

            state.steps.append({"action": action, "args": args, "observation": observation})
            # Verso la UI mostriamo l'osservazione con i nomi reali (deanonimizzata):
            # resta comunque redatta dai segreti.
            emit(
                type="observation",
                action=action,
                text=self.anonymizer.deanonymize(observation) if self.anonymizer else observation,
            )
            obs_msg = f"Observation: {observation}"
            self._conversation.append({"role": "user", "content": obs_msg})
            self.memory.add("user", obs_msg)

        # Finestra scorrevole: evita la crescita illimitata del contesto tra i turni.
        self._trim_history()

        # Rigenera la documentazione se qualcosa è stato modificato nel turno.
        if self._docs_dirty and self.docs:
            try:
                self.docs.save()
            except Exception:  # noqa: BLE001 - la doc non deve rompere il turno
                pass
            self._docs_dirty = False

        if state.final_answer is None:
            summary = self._progress_summary(state)
            summary = self.anonymizer.deanonymize(summary) if self.anonymizer else summary
            emit(type="final", text=summary, complete=False)
            return summary
        # Ripristina i nomi reali nella risposta mostrata all'utente.
        final = (
            self.anonymizer.deanonymize(state.final_answer)
            if self.anonymizer
            else state.final_answer
        )
        emit(type="final", text=final, complete=True)
        return final
