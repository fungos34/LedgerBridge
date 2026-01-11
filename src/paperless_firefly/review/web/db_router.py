"""
Database router for SparkLink state store models.

Routes state store models (PaperlessDocument, Extraction, etc.) 
to the state.db SQLite database while keeping Django's built-in 
models (User, UserProfile, sessions) in the default database.
"""


class StateStoreRouter:
    """
    A router to control database operations for state store models.
    
    State store models are defined with managed=False and use a
    separate SQLite database (state.db) from Django's default database.
    """
    
    # Models that should use the state store database
    STATE_STORE_MODELS = {
        'paperlessdocument',
        'extraction', 
        'import',
        'fireflycache',
        'matchproposal',
        'vendormapping',
        'interpretationrun',
        'bankmatch',
    }
    
    def db_for_read(self, model, **hints):
        """Route read operations for state store models to state_store db."""
        if model._meta.model_name in self.STATE_STORE_MODELS:
            return 'state_store'
        return None
    
    def db_for_write(self, model, **hints):
        """Route write operations for state store models to state_store db."""
        if model._meta.model_name in self.STATE_STORE_MODELS:
            return 'state_store'
        return None
    
    def allow_relation(self, obj1, obj2, **hints):
        """
        Allow relations only if both models are in the same database.
        
        State store models can relate to each other, and default models
        can relate to each other, but cross-database relations are not allowed.
        """
        obj1_is_state = obj1._meta.model_name in self.STATE_STORE_MODELS
        obj2_is_state = obj2._meta.model_name in self.STATE_STORE_MODELS
        
        if obj1_is_state and obj2_is_state:
            return True
        if not obj1_is_state and not obj2_is_state:
            return True
        return False
    
    def allow_migrate(self, db, app_label, model_name=None, **hints):
        """
        State store models are unmanaged (managed=False), so migrations
        are not needed. Only allow migrations on default database.
        """
        if model_name and model_name.lower() in self.STATE_STORE_MODELS:
            # State store models should never be migrated by Django
            return False
        if db == 'state_store':
            # Don't migrate anything to state_store - it's managed separately
            return False
        return None
