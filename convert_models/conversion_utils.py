
from transformers import AutoTokenizer
import json
import warnings
import torch

class ConversionScript():

    _ARCHITECTURE_TYPE_DICT = {}
    _ARCHITECTURE_TYPE_DICT = {**{"LSG" + k: v for k, v in _ARCHITECTURE_TYPE_DICT.items()}, **_ARCHITECTURE_TYPE_DICT}
    _BASE_ARCHITECTURE_TYPE = None
    _DEFAULT_ARCHITECTURE_TYPE = None
    _CONFIG_MODULE = None

    _DEFAULT_CONFIG_POSITIONAL_OFFSET = 0
    _DEFAULT_POSITIONAL_OFFSET = 0

    def __init__(
        self, 
        initial_model, 
        model_name, 
        max_sequence_length, 
        architecture, 
        random_global_init, 
        global_positional_stride, 
        keep_first_global_token, 
        resize_lsg, 
        model_kwargs, 
        config,
        seed
        ):
        
        self.initial_model = initial_model
        self.model_name = model_name
        self.max_sequence_length = max_sequence_length
        self.architecture = architecture
        self.random_global_init = random_global_init
        self.global_positional_stride = global_positional_stride
        self.keep_first_global_token = keep_first_global_token
        self.resize_lsg = resize_lsg
        self.model_kwargs = model_kwargs
        self.config = config

    def save(self, model, tokenizer):

        model.save_pretrained(self.model_name)
        tokenizer.save_pretrained(self.model_name)

    def process(self):
        
        _architecture, _model = self.get_architecture()
        is_base_architecture, is_lsg, keep_first_global = self.get_additional_params(_architecture)
        model, tokenizer = self.get_model(_architecture, _model)
        model, tokenizer = self.update_config(model, tokenizer)

        # Get the module prefix to update
        module_prefix = self.get_module(model, is_base_architecture)

        # Update global embedding
        if not (is_lsg and self.resize_lsg):
            bos_id = tokenizer.bos_token_id if tokenizer.bos_token_id is not None else tokenizer.cls_token_id
            bos_id = bos_id if bos_id is not None else model.config.bos_token_id
            mask_id = tokenizer.mask_token_id
            if self.random_global_init:
                self.update_global_randomly(module_prefix, bos_id, self.global_positional_stride, keep_first_global)
            else:
                self.update_global(module_prefix, bos_id, mask_id, self.global_positional_stride, keep_first_global)

        # Update positional
        self.update_positions(module_prefix, self.max_sequence_length)

        # For Pegasus
        self.update_positions_with_model(model, self.max_sequence_length)
    
        self.save(model, tokenizer)

    def get_architecture(self):
        if self.architecture is not None:
            return self.validate_architecture(self.architecture)

        architectures = self.config.architectures
        if architectures is not None:
            architecture = architectures if isinstance(architectures, str) else architectures[0]
            return self.validate_architecture(architecture)

        return self.validate_architecture(self._DEFAULT_ARCHITECTURE_TYPE)

    def validate_architecture(self, model_type):
        _architecture = self._ARCHITECTURE_TYPE_DICT.get(model_type, None)

        s = "\n * " + "\n * ".join([k for k in self._ARCHITECTURE_TYPE_DICT.keys()])
        assert _architecture is not None, f"Provided/config architecture is wrong, make sure it is in: {s}"
        return _architecture

    def get_model(self, _architecture, _model):
        config = self._CONFIG_MODULE.from_pretrained(
            self.initial_model, 
            architectures=_architecture, 
            trust_remote_code=True, 
            **json.loads(self.model_kwargs.replace("'", "\""))
            )
        model = _model.from_pretrained(self.initial_model, use_auth_token=True, config=config)
        tokenizer = AutoTokenizer.from_pretrained(self.initial_model, use_auth_token=True, trust_remote_code=True)
        return model, tokenizer

    def update_config(self, model, tokenizer):

        # Update tokenizer and config
        tokenizer.model_max_length = self.max_sequence_length
        tokenizer.init_kwargs['model_max_length'] = self.max_sequence_length

        max_pos = self.max_sequence_length
        model.config.max_position_embeddings = max_pos + self._DEFAULT_CONFIG_POSITIONAL_OFFSET
        model.config._name_or_path = self.model_name
        return model, tokenizer

    def get_additional_params(self, _architecture):

        # Hack because of architecture
        is_base_architecture = True if _architecture in [self._BASE_ARCHITECTURE_TYPE, "LSG" + self._BASE_ARCHITECTURE_TYPE] else False

        # Check if it is LSG architecture
        if vars(self.config).get("base_model_prefix", None) == "lsg" and "LSG" in _architecture:
            is_lsg_architecture = True
        else: 
            is_lsg_architecture = False

        if is_lsg_architecture and not self.resize_lsg:
            warnings.warn("LSG architecture detected, to resize positional embedding only, add --resize_lsg (won't affect global embedding)")
        if is_lsg_architecture and not self.keep_first_global_token:
            warnings.warn("LSG architecture detected, to keep the same first global token, add --keep_first_global_token")

        keep_first = False
        if self.keep_first_global_token:
            if is_lsg_architecture:
                keep_first = True
            else:
                warnings.warn("--keep_first_global_token won't be used if the initial model isn't a LSG model")
        return is_base_architecture, is_lsg_architecture, keep_first

    def get_module(self, model, is_base_architecture):
        if is_base_architecture:
            return
        return

    def update_global_randomly(self, module_prefix, bos_id, stride, keep_first_global):
        pass

    def update_global(self, module_prefix, bos_id, mask_id, stride, keep_first_global):
        pass

    def update_positions(self, module_prefix, max_pos):
        pass
    
    def update_positions_with_model(self, model, max_pos):
        pass

    def order_positions(self, positions, stride):
        n, d = positions.size()
        if n % 512 != 0:
            if n > 512:
                positions = positions[:512*(n//512)]
            else:
                mean = positions.mean(dim=0, keepdim=True).expand(512 - n, -1)
                std = positions.std(dim=0, keepdim=True).expand(512 - n, -1)
                positions = torch.cat([positions, torch.normal(mean, std)], dim=0)
            n, d = positions.size()

        factor = n // 512
        positions = positions.reshape(-1, factor, d)[:, 0]
        positions = positions.reshape(-1, stride//factor, d).transpose(0, 1).reshape(-1, d)
        return positions